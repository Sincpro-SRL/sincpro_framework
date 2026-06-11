"""
Tests for the observability/tracing module.

Tests NOT marked "(OTel)" run always — they validate log correlation and
framework context injection which work without opentelemetry installed.

Tests marked "(OTel)" require opentelemetry-sdk and use the otel_setup fixture
from conftest.py. They are skipped automatically if the extra is not installed.
"""

import pytest

from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import DataTransferObject, Feature, UseFramework

# ---------------------------------------------------------------------------
# Shared test setup — module-level framework instance
# ---------------------------------------------------------------------------


class SimpleDTO(DataTransferObject):
    value: str


class SimpleResponse(DataTransferObject):
    result: str


class ErrorDTO(DataTransferObject):
    trigger: str


framework = UseFramework("test-tracing", log_after_execution=False)


@framework.feature(SimpleDTO)
class SimpleFeature(Feature):
    def execute(self, dto: SimpleDTO) -> SimpleResponse:
        return SimpleResponse(result=dto.value)


@framework.feature(ErrorDTO)
class ErrorFeature(Feature):
    def execute(self, dto: ErrorDTO) -> None:
        raise ValueError(f"intentional error: {dto.trigger}")


# ---------------------------------------------------------------------------
# Log correlation tests — no OTel required
# ---------------------------------------------------------------------------


def test_with_trace_no_error():
    """Framework executes correctly inside with_trace()."""
    res: SimpleResponse | None = None
    with framework.with_trace() as traced:
        res = traced(SimpleDTO(value="hello"), SimpleResponse)
    assert res is not None
    assert res.result == "hello"


def test_with_trace_generates_ids_automatically():
    """with_trace() with no args auto-generates trace_id and span_id."""
    with framework.with_trace() as traced:
        ctx = traced._get_context()
        assert "trace_id" in ctx
        assert "span_id" in ctx
        assert len(ctx["trace_id"]) > 0
        assert len(ctx["span_id"]) > 0


def test_with_trace_uses_explicit_ids():
    """with_trace(trace_id, span_id) binds the provided values."""
    with framework.with_trace(trace_id="my-trace-123", span_id="my-span-456") as traced:
        ctx = traced._get_context()
        assert ctx["trace_id"] == "my-trace-123"
        assert ctx["span_id"] == "my-span-456"


def test_trace_ids_in_framework_context():
    """trace_id and span_id are accessible in Feature via self.context."""
    captured: dict = {}

    fw = UseFramework("test-ctx-injection", log_after_execution=False)

    class CaptureDTO(DataTransferObject):
        pass

    class CaptureResponse(DataTransferObject):
        trace_id: str
        span_id: str

    @fw.feature(CaptureDTO)
    class CaptureFeature(Feature):
        def execute(self, dto: CaptureDTO) -> CaptureResponse:
            captured["trace_id"] = self.context.get("trace_id", "")
            captured["span_id"] = self.context.get("span_id", "")
            return CaptureResponse(
                trace_id=self.context.get("trace_id", ""),
                span_id=self.context.get("span_id", ""),
            )

    with fw.with_trace(trace_id="abc-trace", span_id="abc-span") as traced:
        traced(CaptureDTO(), CaptureResponse)

    assert captured["trace_id"] == "abc-trace"
    assert captured["span_id"] == "abc-span"


def test_logs_have_trace_id_inside_block():
    """Logger _temporal_fields contain trace_id/span_id inside the with_trace block."""
    with framework.with_trace(trace_id="log-trace", span_id="log-span"):
        fields = framework.logger.logger_fields
        assert fields.get("trace_id") == "log-trace"
        assert fields.get("span_id") == "log-span"


def test_log_fields_cleared_after_exit():
    """trace_id/span_id do NOT leak outside the with_trace block."""
    with framework.with_trace(trace_id="ephemeral-trace", span_id="ephemeral-span"):
        pass

    fields = framework.logger.logger_fields
    assert "trace_id" not in fields
    assert "span_id" not in fields


def test_framework_context_cleared_after_exit():
    """Framework context is fully restored after with_trace exits."""
    assert "trace_id" not in framework._get_context()

    with framework.with_trace():
        assert "trace_id" in framework._get_context()

    assert "trace_id" not in framework._get_context()


def test_context_api_composable():
    """with_trace() and context() compose correctly."""
    with framework.with_trace(trace_id="composed-trace") as traced:
        with traced.context({"user_id": "u-1"}) as app:
            ctx = app._get_context()
            assert ctx["trace_id"] == "composed-trace"
            assert ctx["user_id"] == "u-1"


def test_context_restored_after_nested_exit():
    """After nested with_trace + context(), parent context is restored correctly."""
    with framework.with_trace(trace_id="outer-trace") as traced:
        with traced.context({"extra": "data"}) as app:
            pass
        ctx = traced._get_context()
        assert ctx.get("trace_id") == "outer-trace"
        assert "extra" not in ctx

    assert "trace_id" not in framework._get_context()


def test_with_trace_error_propagates():
    """Exceptions raised inside with_trace() propagate normally."""
    with pytest.raises(ValueError, match="intentional error"):
        with framework.with_trace() as traced:
            traced(ErrorDTO(trigger="boom"))


def test_with_trace_different_trace_per_call():
    """Each with_trace() call without explicit ids generates unique trace_ids."""
    ids = []
    for _ in range(3):
        with framework.with_trace() as traced:
            ids.append(traced._get_context()["trace_id"])
    assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# OTel span tests — require opentelemetry-sdk (fixtures in conftest.py)
# ---------------------------------------------------------------------------


def test_otel_span_name_equals_dto_name(otel_setup):
    """(OTel) Span name matches dto.__class__.__name__."""
    fw = UseFramework("otel-span-name", log_after_execution=False)

    class SpanNameDTO(DataTransferObject):
        x: int

    class SpanNameResponse(DataTransferObject):
        y: int

    @fw.feature(SpanNameDTO)
    class SpanNameFeature(Feature):
        def execute(self, dto: SpanNameDTO) -> SpanNameResponse:
            return SpanNameResponse(y=dto.x * 2)

    with fw.with_trace() as traced:
        traced(SpanNameDTO(x=1), SpanNameResponse)

    span_names = [s.name for s in otel_setup.get_finished_spans()]
    assert "SpanNameDTO" in span_names


def test_otel_span_has_layer_attribute(otel_setup):
    """(OTel) sincpro.layer attribute is set on feature spans."""
    fw = UseFramework("otel-layer-attr", log_after_execution=False)

    class LayerDTO(DataTransferObject):
        pass

    @fw.feature(LayerDTO)
    class LayerFeature(Feature):
        def execute(self, dto: LayerDTO) -> None:
            return None

    with fw.with_trace() as traced:
        traced(LayerDTO())

    feature_spans = [s for s in otel_setup.get_finished_spans() if s.name == "LayerDTO"]
    assert len(feature_spans) == 1
    assert feature_spans[0].attributes.get("sincpro.layer") == "feature"


def test_otel_app_service_child_spans(otel_setup):
    """(OTel) Feature spans are children of the ApplicationService span."""
    fw = UseFramework("otel-child-spans", log_after_execution=False)

    class ChildFeatureDTO(DataTransferObject):
        pass

    class ChildFeatureResponse(DataTransferObject):
        done: bool

    class ParentAppServiceDTO(DataTransferObject):
        pass

    @fw.feature(ChildFeatureDTO)
    class ChildFeature(Feature):
        def execute(self, dto: ChildFeatureDTO) -> ChildFeatureResponse:
            return ChildFeatureResponse(done=True)

    @fw.app_service(ParentAppServiceDTO)
    class ParentAppService(_ApplicationService):
        def execute(self, dto: ParentAppServiceDTO) -> ChildFeatureResponse:
            return self.feature_bus.execute(ChildFeatureDTO(), ChildFeatureResponse)

    with fw.with_trace() as traced:
        traced(ParentAppServiceDTO(), ChildFeatureResponse)

    spans = otel_setup.get_finished_spans()
    app_spans = [s for s in spans if s.name == "ParentAppServiceDTO"]
    feature_spans = [s for s in spans if s.name == "ChildFeatureDTO"]

    assert len(app_spans) == 1
    assert len(feature_spans) == 1
    assert feature_spans[0].parent is not None
    assert feature_spans[0].parent.span_id == app_spans[0].context.span_id


def test_otel_root_span_is_container(otel_setup):
    """(OTel) with_trace() with no args creates a root span named after the UseFramework instance."""
    fw = UseFramework("my-bounded-context", log_after_execution=False)

    class RootDTO(DataTransferObject):
        pass

    @fw.feature(RootDTO)
    class RootFeature(Feature):
        def execute(self, dto: RootDTO) -> None:
            return None

    with fw.with_trace() as traced:
        traced(RootDTO())

    spans = otel_setup.get_finished_spans()
    root_spans = [s for s in spans if s.name == "my-bounded-context"]
    feature_spans = [s for s in spans if s.name == "RootDTO"]

    assert len(root_spans) == 1
    assert len(feature_spans) == 1
    assert feature_spans[0].parent.span_id == root_spans[0].context.span_id


def test_otel_adopts_outer_active_span(otel_setup):
    """(OTel) W3C traceparent from carrier is used as parent when calling with_trace(carrier=...)."""
    from opentelemetry import trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    fw = UseFramework("otel-propagation", log_after_execution=False)

    class PropagationDTO(DataTransferObject):
        pass

    @fw.feature(PropagationDTO)
    class PropagationFeature(Feature):
        def execute(self, dto: PropagationDTO) -> None:
            return None

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("outer-span") as outer:
        carrier: dict = {}
        TraceContextTextMapPropagator().inject(carrier)
        outer_trace_id = outer.get_span_context().trace_id

    with fw.with_trace(carrier=carrier) as traced:
        traced(PropagationDTO())

    feature_spans = [s for s in otel_setup.get_finished_spans() if s.name == "PropagationDTO"]
    assert len(feature_spans) == 1
    assert feature_spans[0].context.trace_id == outer_trace_id
    # The feature span is a child of the outer span, not just the same trace
    assert feature_spans[0].parent is not None
    assert feature_spans[0].parent.span_id == outer.get_span_context().span_id


def test_otel_error_recorded_in_span(otel_setup):
    """(OTel) Exception sets span status ERROR and records the exception event."""
    from opentelemetry.trace import StatusCode

    fw = UseFramework("otel-error", log_after_execution=False)

    class OtelErrorDTO(DataTransferObject):
        pass

    @fw.feature(OtelErrorDTO)
    class OtelErrorFeature(Feature):
        def execute(self, dto: OtelErrorDTO) -> None:
            raise RuntimeError("otel error test")

    with pytest.raises(RuntimeError):
        with fw.with_trace() as traced:
            traced(OtelErrorDTO())

    error_spans = [s for s in otel_setup.get_finished_spans() if s.name == "OtelErrorDTO"]
    assert len(error_spans) == 1
    assert error_spans[0].status.status_code == StatusCode.ERROR


def test_otel_trace_id_matches_log_trace_id(otel_setup):
    """(OTel) trace_id in framework context matches the OTel trace_id of the root span."""
    fw = UseFramework("otel-id-match", log_after_execution=False)

    class IdMatchDTO(DataTransferObject):
        pass

    @fw.feature(IdMatchDTO)
    class IdMatchFeature(Feature):
        def execute(self, dto: IdMatchDTO) -> None:
            return None

    log_trace_id: str | None = None
    with fw.with_trace() as traced:
        log_trace_id = traced._get_context().get("trace_id")
        traced(IdMatchDTO())

    root_spans = [s for s in otel_setup.get_finished_spans() if s.name == "otel-id-match"]
    assert len(root_spans) == 1
    assert format(root_spans[0].context.trace_id, "032x") == log_trace_id


# ---------------------------------------------------------------------------
# Auto-adoption tests — trace propagation WITHOUT explicit with_trace()
# ---------------------------------------------------------------------------


def test_direct_call_inherits_outer_otel_span(otel_setup):
    """(OTel) Calling framework(dto) directly — without with_trace() — inherits the
    active OTel trace from the outer context (e.g. FastAPI middleware).

    This is the common production case: the outer framework sets the trace and
    the framework picks it up automatically, no explicit with_trace() needed.
    """
    from opentelemetry import trace

    fw = UseFramework("direct-call", log_after_execution=False)

    class DirectDTO(DataTransferObject):
        pass

    captured_log_fields: dict = {}

    @fw.feature(DirectDTO)
    class DirectFeature(Feature):
        def execute(self, dto: DirectDTO) -> None:
            # During execution the shared logger should have the outer trace_id
            captured_log_fields.update(fw.logger.logger_fields)
            return None

    tracer = trace.get_tracer("outer-framework")
    with tracer.start_as_current_span("http-request") as outer:
        outer_trace_id = format(outer.get_span_context().trace_id, "032x")
        fw(DirectDTO())  # ← no with_trace(), direct call

    # Logger had the OTel trace_id during execution
    assert "trace_id" in captured_log_fields
    assert captured_log_fields["trace_id"] == outer_trace_id

    # The feature span is on the same trace as the outer http-request span
    feature_spans = [s for s in otel_setup.get_finished_spans() if s.name == "DirectDTO"]
    assert len(feature_spans) == 1
    assert format(feature_spans[0].context.trace_id, "032x") == outer_trace_id


def test_direct_call_creates_own_span_when_no_outer_trace(otel_setup):
    """(OTel) Without with_trace() or outer span, the bus creates its own root span
    and logs have the matching trace_id."""
    fw = UseFramework("auto-root", log_after_execution=False)

    class NoOuterDTO(DataTransferObject):
        pass

    captured: dict = {}

    @fw.feature(NoOuterDTO)
    class NoOuterFeature(Feature):
        def execute(self, dto: NoOuterDTO) -> None:
            captured.update(fw.logger.logger_fields)
            return None

    fw(NoOuterDTO())  # No with_trace(), no outer span

    assert "trace_id" in captured

    feature_spans = [s for s in otel_setup.get_finished_spans() if s.name == "NoOuterDTO"]
    assert len(feature_spans) == 1
    assert format(feature_spans[0].context.trace_id, "032x") == captured["trace_id"]


def test_log_fields_not_leaked_after_direct_call(otel_setup):
    """(OTel) trace_id/span_id are not present in the logger AFTER a direct call ends."""
    fw = UseFramework("leak-check", log_after_execution=False)

    class LeakDTO(DataTransferObject):
        pass

    @fw.feature(LeakDTO)
    class LeakFeature(Feature):
        def execute(self, dto: LeakDTO) -> None:
            return None

    fw(LeakDTO())

    # After execution, logger should have no trace context
    assert "trace_id" not in fw.logger.logger_fields
    assert "span_id" not in fw.logger.logger_fields


# ---------------------------------------------------------------------------
# sincpro.instance attribute — distinguishes N UseFramework instances
# ---------------------------------------------------------------------------


def test_otel_span_has_instance_attribute(otel_setup):
    """(OTel) sincpro.instance is set on spans and equals the bounded-context name."""
    fw = UseFramework("my-billing-context", log_after_execution=False)

    class BillingDTO(DataTransferObject):
        pass

    @fw.feature(BillingDTO)
    class BillingFeature(Feature):
        def execute(self, dto: BillingDTO) -> None:
            return None

    with fw.with_trace() as traced:
        traced(BillingDTO())

    feature_spans = [s for s in otel_setup.get_finished_spans() if s.name == "BillingDTO"]
    assert len(feature_spans) == 1
    assert feature_spans[0].attributes.get("sincpro.instance") == "my-billing-context"


def test_otel_instance_attribute_differs_per_framework(otel_setup):
    """(OTel) Two UseFramework instances produce spans with different sincpro.instance values."""
    fw_a = UseFramework("context-alpha", log_after_execution=False)
    fw_b = UseFramework("context-beta", log_after_execution=False)

    class AlphaDTO(DataTransferObject):
        pass

    class BetaDTO(DataTransferObject):
        pass

    @fw_a.feature(AlphaDTO)
    class AlphaFeature(Feature):
        def execute(self, dto: AlphaDTO) -> None:
            return None

    @fw_b.feature(BetaDTO)
    class BetaFeature(Feature):
        def execute(self, dto: BetaDTO) -> None:
            return None

    with fw_a.with_trace() as traced:
        traced(AlphaDTO())
    with fw_b.with_trace() as traced:
        traced(BetaDTO())

    alpha_spans = [s for s in otel_setup.get_finished_spans() if s.name == "AlphaDTO"]
    beta_spans = [s for s in otel_setup.get_finished_spans() if s.name == "BetaDTO"]
    assert alpha_spans[0].attributes.get("sincpro.instance") == "context-alpha"
    assert beta_spans[0].attributes.get("sincpro.instance") == "context-beta"


# ---------------------------------------------------------------------------
# with_parent_trace() — adopt the host application's active span
# ---------------------------------------------------------------------------


def test_with_parent_trace_generates_ids_without_otel():
    """with_parent_trace() without OTel still provides UUID-based log correlation."""
    fw = UseFramework("parent-trace-no-otel", log_after_execution=False)

    class NoOtelDTO(DataTransferObject):
        pass

    @fw.feature(NoOtelDTO)
    class NoOtelFeature(Feature):
        def execute(self, dto: NoOtelDTO) -> None:
            return None

    with fw.with_parent_trace() as traced:
        ctx = traced._get_context()
        assert "trace_id" in ctx
        assert "span_id" in ctx
        assert len(ctx["trace_id"]) > 0


def test_with_parent_trace_adopts_active_span_ids(otel_setup):
    """(OTel) with_parent_trace() binds the active span's trace_id/span_id to
    the framework context and logger — same IDs as the host span, no new span."""
    from opentelemetry import trace as otel_trace

    fw = UseFramework("parent-trace-adopt", log_after_execution=False)

    tracer = otel_trace.get_tracer("test-host")
    with tracer.start_as_current_span("odoo-controller") as host_span:
        expected_trace_id = format(host_span.get_span_context().trace_id, "032x")
        expected_span_id = format(host_span.get_span_context().span_id, "016x")

        with fw.with_parent_trace() as traced:
            ctx = traced._get_context()
            assert ctx["trace_id"] == expected_trace_id
            assert ctx["span_id"] == expected_span_id
            assert fw.logger.logger_fields["trace_id"] == expected_trace_id


def test_with_parent_trace_dto_is_direct_child_no_root_span(otel_setup):
    """(OTel) DTO spans inside with_parent_trace() are direct children of the host
    span — no intermediate root span is added by the framework."""
    from opentelemetry import trace as otel_trace

    fw = UseFramework("parent-trace-direct-child", log_after_execution=False)

    class DirectChildDTO(DataTransferObject):
        pass

    @fw.feature(DirectChildDTO)
    class DirectChildFeature(Feature):
        def execute(self, dto: DirectChildDTO) -> None:
            return None

    tracer = otel_trace.get_tracer("test-host")
    with tracer.start_as_current_span("odoo-controller") as host_span:
        with fw.with_parent_trace() as traced:
            traced(DirectChildDTO())

    spans = otel_setup.get_finished_spans()
    dto_spans = [s for s in spans if s.name == "DirectChildDTO"]
    root_spans = [s for s in spans if s.name == "parent-trace-direct-child"]

    assert len(dto_spans) == 1
    assert (
        len(root_spans) == 0
    ), "with_parent_trace() must not create an intermediate root span"
    assert dto_spans[0].parent is not None
    assert dto_spans[0].parent.span_id == host_span.get_span_context().span_id


def test_with_parent_trace_fallback_uuids_when_no_active_span(otel_setup):
    """(OTel) with_parent_trace() falls back to fresh UUIDs when no span is active."""
    fw = UseFramework("parent-trace-fallback", log_after_execution=False)

    with fw.with_parent_trace() as traced:
        ctx = traced._get_context()
        assert "trace_id" in ctx
        # UUID-based IDs are 36 chars; OTel hex trace_ids are 32 chars — both > 0
        assert len(ctx["trace_id"]) > 0


# ---------------------------------------------------------------------------
# carrier without OTel — warning test (no otel_setup fixture needed)
# ---------------------------------------------------------------------------


def test_carrier_without_otel_emits_warning(monkeypatch):
    """carrier=... without OTel installed emits RuntimeWarning and falls back to UUIDs."""
    import sincpro_framework.tracing.span_context as sc_module

    monkeypatch.setattr(sc_module, "_OTEL_AVAILABLE", False)

    fw = UseFramework("warn-test", log_after_execution=False)

    class WarnDTO(DataTransferObject):
        pass

    @fw.feature(WarnDTO)
    class WarnFeature(Feature):
        def execute(self, dto: WarnDTO) -> None:
            return None

    with pytest.warns(RuntimeWarning, match="opentelemetry is not installed"):
        with fw.with_trace(carrier={"traceparent": "00-abc-def-01"}) as traced:
            # trace IDs are still generated (UUIDs), execution completes normally
            assert "trace_id" in traced._get_context()
            traced(WarnDTO())
