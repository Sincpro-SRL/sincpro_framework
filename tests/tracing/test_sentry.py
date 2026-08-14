"""Sentry/GlitchTip auto-instrumentation — silent when SDK or DSN is absent."""

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.tracing.sentry import record_sentry_error, setup_sentry


class BoomDTO(DataTransferObject):
    pass


def test_setup_sentry_silent_without_dsn(monkeypatch):
    from sincpro_framework.tracing import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    setup_sentry("test-bc")


def test_setup_sentry_does_not_init_when_host_already_configured(monkeypatch):
    """Host called sentry_sdk.init; SENTRY_DSN may still be unset. Do not re-init."""
    from sincpro_framework.tracing import sentry as sentry_mod

    inits: list[str] = []

    class FakeSentry:
        @staticmethod
        def init(**kwargs):
            inits.append("init")

        @staticmethod
        def set_tag(key, value):
            pass

    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", "https://key@host/1")
    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod, "_client_is_active", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", FakeSentry)

    setup_sentry("payments")
    assert inits == []


def test_record_sentry_without_dsn_if_host_client_is_active(monkeypatch):
    """Odoo inited Sentry with its own DSN — framework env SENTRY_DSN is empty."""
    from sincpro_framework.tracing import sentry as sentry_mod

    captured: list[Exception] = []

    class FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_tag(self, key, value):
            pass

    class FakeSentry:
        @staticmethod
        def new_scope():
            return FakeScope()

        @staticmethod
        def capture_exception(error):
            captured.append(error)

    monkeypatch.setattr(sentry_mod.settings, "sentry_dsn", None)
    monkeypatch.setattr(sentry_mod, "_SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr(sentry_mod, "_client_is_active", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", FakeSentry)

    err = RuntimeError("from-odoo-init")
    record_sentry_error(err, "CreateOrderDTO", "feature", "payments")
    assert captured == [err]


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


def test_record_sentry_captures_when_active(monkeypatch):
    captured: list[Exception] = []
    tags: dict[str, str] = {}

    class FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_tag(self, key, value):
            tags[key] = value

    class FakeSentry:
        @staticmethod
        def new_scope():
            return FakeScope()

        @staticmethod
        def capture_exception(error):
            captured.append(error)

    monkeypatch.setattr("sincpro_framework.tracing.sentry._SENTRY_SDK_AVAILABLE", True)
    monkeypatch.setattr("sincpro_framework.tracing.sentry._client_is_active", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", FakeSentry)

    err = ValueError("from-bus")
    record_sentry_error(err, "CreateOrderDTO", "feature", "payments")

    assert captured == [err]
    assert tags["sincpro.layer"] == "feature"
    assert tags["sincpro.dto"] == "CreateOrderDTO"
    assert tags["sincpro.instance"] == "payments"

    record_sentry_error(err, "OtherDTO", "application_service", "payments")
    assert captured == [err]
    assert tags["sincpro.dto"] == "CreateOrderDTO"
