"""Sentry/GlitchTip auto-instrumentation — silent when SDK or DSN is absent."""

from importlib.metadata import version as installed_version
from typing import Any, Dict, List

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.exceptions import UnknownDTOToExecute
from sincpro_framework.observability import ObservabilityIdentity, registry, resolve_identity
from sincpro_framework.observability.domain import framework_identity
from sincpro_framework.observability.domain import installed_version as dist_version
from sincpro_framework.observability.errors.record_error import record_error
from sincpro_framework.observability.errors.setup import setup
from sincpro_framework.sincpro_conf import settings


def identity_of(bus: str, version: str = "unknown", artifact: str = "unknown"):
    return ObservabilityIdentity(artifact=artifact, version=version, bus=bus)


class BoomDTO(DataTransferObject):
    pass


class ValidationError(Exception):
    pass


class CaptureState:
    def __init__(self) -> None:
        self.errors: List[Exception] = []
        self.tags: Dict[str, str] = {}
        self.release: str = ""
        self.inits: List[str] = []
        self.clients: List[Dict[str, Any]] = []
        self.layers: List[str] = []


def _install_fake_sentry(monkeypatch, state: CaptureState) -> None:
    from sincpro_framework.observability.errors import setup as sentry_mod

    class FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_client(self, client: Any) -> None:
            if getattr(client, "release", None):
                state.release = client.release

        def set_tag(self, key: str, value: str) -> None:
            state.tags[key] = value
            if key == "sincpro.layer":
                state.layers.append(value)

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.release = kwargs.get("release", "")
            state.release = self.release
            state.clients.append(kwargs)

    class FakeSentry:
        Client = FakeClient

        @staticmethod
        def init(**kwargs: Any) -> None:
            state.inits.append("init")

        @staticmethod
        def isolation_scope() -> FakeScope:
            return FakeScope()

        @staticmethod
        def capture_exception(error: Exception) -> None:
            state.errors.append(error)

    registry.reset()
    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "https://key@glitchtip.example/1")
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", FakeSentry)


def test_setup_sentry_silent_without_dsn(monkeypatch):
    from sincpro_framework.observability.errors import setup as sentry_mod

    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    status = setup(resolve_identity("test-bc"))
    assert status.state == "off"
    assert status.reason == "dsn_missing"


def test_setup_sentry_never_calls_global_init(monkeypatch):
    """Framework must not call sentry_sdk.init — that would overwrite Odoo."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    status = setup(
        identity_of("payment-cybersource", version="5.0.3", artifact="sincpro-payments-sdk")
    )
    assert state.inits == []
    assert status.state == "on"
    assert status.reason == "init"
    assert state.clients[0]["release"] == "sincpro-payments-sdk:5.0.3"
    assert state.clients[0]["traces_sample_rate"] == 0.0
    assert state.clients[0]["auto_enabling_integrations"] is False


def test_record_sentry_without_dsn_does_not_capture(monkeypatch):
    """Without conf DSN the framework does not piggyback the host client."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    from sincpro_framework.observability.errors import setup as sentry_mod

    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    registry.reset()

    record_error(
        RuntimeError("from-odoo-init"), "CreateOrderDTO", "feature", identity_of("payments")
    )
    assert state.errors == []


def test_record_sentry_error_never_raises():
    record_error(ValueError("x"), "BoomDTO", "feature", identity_of("payments"))


def test_bus_error_still_raises_without_sentry():
    """Auto-instrument must not swallow bus errors when Sentry is off."""
    app = UseFramework("test-sentry-noop", log_after_execution=False)

    @app.feature(BoomDTO)
    class Boom(Feature):
        def execute(self, dto: BoomDTO):
            raise RuntimeError("boom")

    try:
        app(BoomDTO())
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert str(exc) == "boom"


def test_record_sentry_captures_when_dsn_configured(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    err = ValueError("from-bus")
    record_error(err, "CreateOrderDTO", "feature", identity_of("payments"))

    assert state.errors == [err]
    assert state.tags["sincpro.kind"] == "instance"
    assert state.tags["sincpro.layer"] == "feature"
    assert state.tags["sincpro.dto"] == "CreateOrderDTO"
    assert state.tags["sincpro.instance"] == "payments"

    record_error(err, "OtherDTO", "application_service", identity_of("payments"))
    assert state.errors == [err, err]
    assert state.tags["sincpro.layer"] == "application_service"


def test_package_version_of_installed_framework():
    assert dist_version("sincpro-framework") == installed_version("sincpro-framework")
    assert dist_version("this-dist-does-not-exist") == ""


def test_framework_release_uses_package_version():
    assert framework_identity().release == (
        f"sincpro-framework:{installed_version('sincpro-framework')}"
    )


def test_service_without_a_distribution_still_gets_a_version(monkeypatch):
    """A service entrypoint isn't an installed distribution — APP_RELEASE answers."""
    monkeypatch.setattr(settings, "app_release", "2026.08.21")
    monkeypatch.setattr(settings, "otel_service_name", None)

    assert resolve_identity("payments").release == "payments:2026.08.21"


def test_without_app_release_the_version_is_unknown(monkeypatch):
    monkeypatch.setattr(settings, "app_release", None)
    monkeypatch.setattr(settings, "otel_service_name", None)
    monkeypatch.setattr(
        "sincpro_framework.observability.domain._from_caller_distribution", lambda: ("", "")
    )

    assert resolve_identity("payments").release == "payments:unknown"


def test_release_is_the_sdk_distribution_and_its_version(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    monkeypatch.setattr(
        "sincpro_framework.observability.domain.installed_version",
        lambda name: "5.0.3" if name == "sincpro-payments-sdk" else "x",
    )

    app = UseFramework(
        "payment-cybersource",
        log_after_execution=False,
        package="sincpro-payments-sdk",
    )

    @app.feature(BoomDTO)
    class Boom(Feature):
        def execute(self, dto: BoomDTO):
            raise RuntimeError("gateway down")

    try:
        app(BoomDTO())
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    assert state.errors
    assert state.release == "sincpro-payments-sdk:5.0.3"
    assert state.tags["sincpro.kind"] == "instance"
    assert state.tags["sincpro.instance"] == "payment-cybersource"
    assert state.tags["sincpro.package"] == "sincpro-payments-sdk"
    assert state.tags["sincpro.layer"] == "feature"
    assert state.tags["sincpro.dto"] == "BoomDTO"


def test_record_error_sends_every_metadata_field(monkeypatch):
    """GlitchTip event carries release, environment, bus, package, layer and DTO."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    monkeypatch.setattr(settings, "tenant", "acme")

    record_error(
        RuntimeError("declined"),
        "CommandPayQR",
        "feature",
        ObservabilityIdentity(
            artifact="sincpro-payments-sdk", version="5.1.0", bus="payment-qr"
        ),
        kind="instance",
    )

    assert state.errors
    assert state.release == "sincpro-payments-sdk:5.1.0"
    assert state.clients[0]["environment"] == "acme"
    assert state.tags == {
        "sincpro.kind": "instance",
        "sincpro.layer": "feature",
        "sincpro.dto": "CommandPayQR",
        "sincpro.instance": "payment-qr",
        "sincpro.package": "sincpro-payments-sdk",
        "tenant": "acme",
    }


def test_tenant_tag_comes_from_conf(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    monkeypatch.setattr(settings, "tenant", "acme")

    record_error(RuntimeError("x"), "BoomDTO", "feature", identity_of("payments"))
    assert state.tags["tenant"] == "acme"
    assert state.clients[0]["environment"] == "acme"


def test_error_handler_still_reports_unexpected(monkeypatch):
    """A handler that swallows the error must not hide it from GlitchTip."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)
    app.add_feature_error_handler(lambda error: "swallowed")

    @app.feature(BoomDTO)
    class Boom(Feature):
        def execute(self, dto: BoomDTO):
            raise RuntimeError("hidden bug")

    result = app(BoomDTO())
    assert result == "swallowed"
    assert len(state.errors) == 1
    assert str(state.errors[0]) == "hidden bug"
    assert state.tags["sincpro.kind"] == "instance"


def test_ignored_exception_is_not_reported(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)
    app.ignore_sentry_exceptions(ValidationError)
    app.add_feature_error_handler(lambda error: "ok")

    @app.feature(BoomDTO)
    class Boom(Feature):
        def execute(self, dto: BoomDTO):
            raise ValidationError("bad card")

    result = app(BoomDTO())
    assert result == "ok"
    assert state.errors == []


def test_framework_error_uses_framework_release(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    class GhostDTO(DataTransferObject):
        pass

    try:
        app(GhostDTO())
        raise AssertionError("expected UnknownDTOToExecute")
    except UnknownDTOToExecute:
        pass

    assert len(state.errors) == 1
    assert isinstance(state.errors[0], UnknownDTOToExecute)
    assert state.tags["sincpro.kind"] == "framework"
    assert state.release == framework_identity().release


def test_framework_error_is_not_affected_by_ignore_list(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)
    app.ignore_sentry_exceptions(Exception)

    class GhostDTO(DataTransferObject):
        pass

    try:
        app(GhostDTO())
        raise AssertionError("expected UnknownDTOToExecute")
    except UnknownDTOToExecute:
        pass

    assert len(state.errors) == 1
    assert state.tags["sincpro.kind"] == "framework"


def test_framework_runs_without_otel_or_sentry_packages(monkeypatch):
    """A client without extras still executes Features. Observability stays off."""
    import sys

    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
    monkeypatch.setattr("sincpro_framework.observability.errors.setup.SDK_AVAILABLE", False)

    fw = UseFramework("no-extras-client", log_after_execution=False)

    @fw.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert fw(BoomDTO()) == "ok"
    status = fw.observability.status
    assert status.sentry.state == "off"
    assert status.otel.state == "off"


def test_status_is_off_without_the_sentry_sdk(monkeypatch):
    from sincpro_framework.observability.errors import setup as sentry_mod

    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", False)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability.status
    assert status.sentry.active is False
    assert status.sentry.state == "off"
    assert status.sentry.reason == "sdk_missing"


def test_status_is_on_when_conf_has_a_dsn(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability.status
    assert status.sentry.active is True
    assert status.sentry.state == "on"
    assert status.sentry.reason == "init"
    assert state.inits == []


def test_client_failure_does_not_break_the_bus(monkeypatch):
    from sincpro_framework.observability.errors import setup as sentry_mod

    class BoomClient:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("bad dsn")

    class BoomSentry:
        Client = BoomClient

        @staticmethod
        def isolation_scope() -> None:
            raise AssertionError("should not isolate if Client failed")

        @staticmethod
        def capture_exception(error: Exception) -> None:
            raise AssertionError("should not capture")

    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", True)
    registry.reset()
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "https://key@host/1")
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", BoomSentry)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability.status
    assert status.sentry.active is False
    assert status.sentry.state == "failed"
    assert "bad dsn" in status.sentry.reason


def test_setup_sentry_reports_dsn_missing(monkeypatch):
    from sincpro_framework.observability.errors import setup as sentry_mod

    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)

    status = setup(resolve_identity("test-bc"))
    assert status.active is False
    assert status.state == "off"
    assert status.reason == "dsn_missing"


def test_status_before_build_is_not_built():
    app = UseFramework("payment-cybersource", log_after_execution=False)
    status = app.observability.status
    assert status.sentry.reason == "not_built"
    assert status.otel.reason == "not_built"


def test_unusable_dsn_is_dsn_missing(monkeypatch):
    from sincpro_framework.observability.errors import setup as sentry_mod

    monkeypatch.setattr(sentry_mod, "SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "not-a-dsn")

    status = setup(resolve_identity("test-bc"))
    assert status.reason == "dsn_missing"


def test_conf_uses_sentry_python_dsn():
    from pathlib import Path

    conf = (
        Path(__file__).resolve().parents[3]
        / "sincpro_framework"
        / "conf"
        / "sincpro_framework_conf.yml"
    )
    text = conf.read_text()
    assert "sentry_dsn: $ENV:SENTRY_PYTHON_DSN" in text


def test_appservice_feature_error_emits_both_layers(monkeypatch):
    """No capture markers: Feature then AppService each send the same error."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    class ChildDTO(DataTransferObject):
        pass

    class ParentDTO(DataTransferObject):
        pass

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(ChildDTO)
    class Child(Feature):
        def execute(self, dto: ChildDTO):
            raise RuntimeError("from-feature")

    @app.app_service(ParentDTO)
    class Parent(ApplicationService):
        def execute(self, dto: ParentDTO):
            return self.feature_bus.execute(ChildDTO())

    try:
        app(ParentDTO())
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    assert len(state.errors) == 2
    assert state.layers == ["feature", "application_service"]
    assert state.release.startswith("payment-cybersource:")
    assert state.inits == []
