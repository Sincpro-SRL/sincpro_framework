"""Sentry/GlitchTip auto-instrumentation — silent when SDK or DSN is absent."""

from importlib.metadata import version as installed_version
from typing import Any, Dict, List

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.exceptions import UnknownDTOToExecute
from sincpro_framework.tracing.sentry import (
    build_release,
    framework_release,
    library_version,
    record_sentry_error,
    setup_sentry,
)


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
    from sincpro_framework.tracing import sentry as sentry_mod

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

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod, "_clients", {})
    monkeypatch.setattr(sentry_mod, "_app_releases", {})
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "https://key@glitchtip.example/1")
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", FakeSentry)


def test_setup_sentry_silent_without_dsn(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    status = setup_sentry("test-bc")
    assert status["state"] == "off"
    assert status["reason"] == "dsn_missing"


def test_setup_sentry_never_calls_global_init(monkeypatch):
    """Framework must not call sentry_sdk.init — that would overwrite Odoo."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    status = setup_sentry("payment-cybersource", release="payment-cybersource:5.0.3")
    assert state.inits == []
    assert status["state"] == "on"
    assert status["reason"] == "init"
    assert state.clients[0]["release"] == "payment-cybersource:5.0.3"
    assert state.clients[0]["traces_sample_rate"] == 0.0
    assert state.clients[0]["auto_enabling_integrations"] is False


def test_record_sentry_without_dsn_does_not_capture(monkeypatch):
    """Without conf DSN the framework does not piggyback the host client."""
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    monkeypatch.setattr(sentry_mod, "_clients", {})

    record_sentry_error(
        RuntimeError("from-odoo-init"), "CreateOrderDTO", "feature", "payments"
    )
    assert state.errors == []


def test_record_sentry_error_never_raises():
    record_sentry_error(ValueError("x"), "BoomDTO", "feature", "payments")


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
    record_sentry_error(err, "CreateOrderDTO", "feature", "payments")

    assert state.errors == [err]
    assert state.tags["sincpro.kind"] == "instance"
    assert state.tags["sincpro.layer"] == "feature"
    assert state.tags["sincpro.dto"] == "CreateOrderDTO"
    assert state.tags["sincpro.instance"] == "payments"

    record_sentry_error(err, "OtherDTO", "application_service", "payments")
    assert state.errors == [err, err]
    assert state.tags["sincpro.layer"] == "application_service"


def test_library_version_of_installed_framework():
    assert library_version("sincpro-framework") == installed_version("sincpro-framework")
    assert library_version("this-dist-does-not-exist") == "unknown"


def test_framework_release_uses_package_version():
    assert framework_release() == build_release(
        "sincpro-framework", installed_version("sincpro-framework")
    )


def test_instance_release_is_app_name_and_library_version(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    monkeypatch.setattr(
        "sincpro_framework.use_bus.library_version",
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
    assert state.release == "payment-cybersource:5.0.3"
    assert state.tags["sincpro.kind"] == "instance"
    assert state.tags["sincpro.instance"] == "payment-cybersource"


def test_tenant_tag_from_env(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    monkeypatch.setenv("TENANT", "acme")

    record_sentry_error(RuntimeError("x"), "BoomDTO", "feature", "payments")
    assert state.tags["tenant"] == "acme"


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
    assert state.release == framework_release()


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


def test_observability_status_off_without_sdk(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", False)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability_status()
    assert status["sentry"]["active"] is False
    assert status["sentry"]["state"] == "off"
    assert status["sentry"]["reason"] == "sdk_missing"


def test_observability_status_on_when_conf_has_dsn(monkeypatch):
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability_status()
    assert status["sentry"]["active"] is True
    assert status["sentry"]["state"] == "on"
    assert status["sentry"]["reason"] == "init"
    assert state.inits == []


def test_client_failure_does_not_break_the_bus(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

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

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod, "_clients", {})
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "https://key@host/1")
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", BoomSentry)

    app = UseFramework("payment-cybersource", log_after_execution=False)

    @app.feature(BoomDTO)
    class Ok(Feature):
        def execute(self, dto: BoomDTO):
            return "ok"

    assert app(BoomDTO()) == "ok"
    status = app.observability_status()
    assert status["sentry"]["active"] is False
    assert status["sentry"]["state"] == "failed"
    assert "bad dsn" in status["sentry"]["reason"]


def test_setup_sentry_reports_dsn_missing(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)

    status = setup_sentry("test-bc")
    assert status["active"] is False
    assert status["state"] == "off"
    assert status["reason"] == "dsn_missing"


def test_observability_status_before_build_is_not_built():
    app = UseFramework("payment-cybersource", log_after_execution=False)
    status = app.observability_status()
    assert status["sentry"]["reason"] == "not_built"
    assert status["otel"]["reason"] == "not_built"


def test_unusable_dsn_is_dsn_missing(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "not-a-dsn")

    status = setup_sentry("test-bc")
    assert status["reason"] == "dsn_missing"


def test_conf_uses_sentry_python_dsn():
    from pathlib import Path

    conf = (
        Path(__file__).resolve().parents[2]
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
