"""End-to-end error handler tests using UseFramework."""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class DomainError(Exception):
    pass


def _make_framework() -> UseFramework:
    return UseFramework("test-error-handlers", log_after_execution=False)


# ---------------------------------------------------------------------------
# Base contracts
# ---------------------------------------------------------------------------
def test_handler_suppresses_exception():
    """A handler that returns a value suppresses the exception."""
    app = _make_framework()

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("boom")

    app.add_global_error_handler(lambda e, n: "handled")
    assert app(Cmd()) == "handled"


def test_no_handler_propagates_original_exception():
    """Without any handler the original exception reaches the caller."""
    app = _make_framework()

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("raw")

    with pytest.raises(DomainError):
        app(Cmd())


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
def test_chained_handlers_last_added_executes_first():
    """Last registered handler executes first; delegates via next_handler."""
    app = _make_framework()
    call_order = []

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("boom")

    def h1(error, next_handler):
        call_order.append("h1")
        return "from-h1"

    def h2(error, next_handler):
        call_order.append("h2")
        return next_handler(error)

    app.add_global_error_handler(h1)
    app.add_global_error_handler(h2)

    assert app(Cmd()) == "from-h1"
    assert call_order == ["h2", "h1"]


def test_outer_handler_can_short_circuit():
    """Outer handler (last added) can resolve without invoking the inner one."""
    app = _make_framework()
    inner_called = []

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("boom")

    def inner(error, next_handler):
        inner_called.append(True)
        return "from-inner"

    app.add_global_error_handler(inner)
    app.add_global_error_handler(lambda e, n: "from-outer")

    assert app(Cmd()) == "from-outer"
    assert inner_called == []


# ---------------------------------------------------------------------------
# Feature and app service level handlers
# ---------------------------------------------------------------------------
def test_feature_error_handler():
    """add_feature_error_handler intercepts errors from features."""
    app = _make_framework()

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("feature error")

    app.add_feature_error_handler(lambda e, n: "feature-handled")
    assert app(Cmd()) == "feature-handled"


def test_app_service_error_handler():
    """add_app_service_error_handler intercepts errors from app services."""
    app = _make_framework()

    class CmdFeature(DataTransferObject):
        pass

    class CmdAppService(DataTransferObject):
        pass

    @app.feature(CmdFeature)
    class OkFeature(Feature):
        def execute(self, dto: CmdFeature):
            return "ok"

    @app.app_service(CmdAppService)
    class RaisingAppService(ApplicationService):
        def execute(self, dto: CmdAppService):
            raise DomainError("app service error")

    app.add_app_service_error_handler(lambda e, n: "app-service-handled")
    assert app(CmdAppService()) == "app-service-handled"


# ---------------------------------------------------------------------------
# Registration lifecycle: pre-build and post-build
# ---------------------------------------------------------------------------
def test_handler_registered_before_build_takes_effect():
    """Handler registered before build_root_bus() is active when the bus starts."""
    app = _make_framework()

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("boom")

    app.add_global_error_handler(lambda e: "pre-build-handled")

    app.build_root_bus()  # explicit build after registration

    assert app(Cmd()) == "pre-build-handled"


def test_handler_registered_after_build_takes_effect():
    """Handler registered after build_root_bus() is propagated to the live bus immediately."""
    app = _make_framework()

    class Cmd(DataTransferObject):
        pass

    @app.feature(Cmd)
    class RaisingFeature(Feature):
        def execute(self, dto: Cmd):
            raise DomainError("boom")

    app.build_root_bus()  # bus already live

    app.add_global_error_handler(lambda e: "post-build-handled")  # register after build

    assert app(Cmd()) == "post-build-handled"
