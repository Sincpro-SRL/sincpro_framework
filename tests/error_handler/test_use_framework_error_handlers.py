"""End-to-end error handler tests using UseFramework."""

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework


class BrokenCommand(DataTransferObject):
    pass


class Answer(DataTransferObject):
    ok: bool = True


class Boom(Exception):
    pass


class ValidationError(Exception):
    pass


class BusinessError(Exception):
    pass


class FatalError(Exception):
    pass


def _make_framework() -> UseFramework:
    return UseFramework("test-error-handlers", log_after_execution=False)


class TestImplicitHandlerChain:
    """Handlers only take (error). On re-raise the framework chains to the next one."""

    def test_execution_order_h1_h2_h3(self):
        """add(h1), add(h2), add(h3) → h1 raises → h2 raises → h3 returns.

        Each handler transforms the exception to a different type.
        """
        app = _make_framework()

        class Cmd(DataTransferObject):
            pass

        @app.feature(Cmd)
        class RaisingFeature(Feature):
            def execute(self, dto: Cmd):
                raise ValidationError("invalid input")

        def h1(error):
            assert isinstance(error, ValidationError)
            raise BusinessError(f"h1 got [{error}]")

        def h2(error):
            assert isinstance(error, BusinessError)
            raise FatalError(f"h2 got [{error}]")

        def h3(error):
            assert isinstance(error, FatalError)
            return f"h3 handled [{error}]"

        app.add_global_error_handler(h1)
        app.add_global_error_handler(h2)
        app.add_global_error_handler(h3)

        result = app(Cmd())
        assert result == "h3 handled [h2 got [h1 got [invalid input]]]"


# --- what a handler returning means, which is the part that catches people out --------------


def test_with_no_handler_the_error_simply_propagates():
    """The baseline. Nothing is registered, so the caller sees what went wrong."""
    bus = UseFramework("plain", log_after_execution=False)

    @bus.feature(BrokenCommand)
    class Breaks(Feature):
        def execute(self, dto: BrokenCommand) -> Answer:
            raise Boom("it broke")

    with pytest.raises(Boom, match="it broke"):
        bus(BrokenCommand())


def test_what_a_handler_returns_becomes_the_answer_and_the_error_is_gone():
    """The documented contract, pinned because it is load-bearing: a handler that answers has
    handled it, and the bus returns what it said."""
    bus = UseFramework("answers", log_after_execution=False)
    bus.add_feature_error_handler(lambda error: Answer(ok=False))

    @bus.feature(BrokenCommand)
    class Breaks(Feature):
        def execute(self, dto: BrokenCommand) -> Answer:
            raise Boom("it broke")

    assert bus(BrokenCommand()) == Answer(ok=False)


def test_a_handler_that_only_watches_must_re_raise_or_the_failure_disappears():
    """**The trap this test exists for.** The common reason to register a handler is to look at
    the error — and a function that only logs returns `None` without meaning to, which the bus
    reads as "handled, and the answer is None". Written with a `raise` at the end, the error is
    seen *and* still an error."""
    seen: list[str] = []

    def only_watches(error: Exception):
        seen.append(str(error))
        # no raise: the failure is now this handler's answer

    swallowing = UseFramework("swallows", log_after_execution=False)
    swallowing.add_feature_error_handler(only_watches)

    @swallowing.feature(BrokenCommand)
    class BreaksOne(Feature):
        def execute(self, dto: BrokenCommand) -> Answer:
            raise Boom("it broke")

    assert swallowing(BrokenCommand()) is None
    assert seen == ["it broke"]

    def watches_and_passes_it_on(error: Exception):
        seen.append(str(error))
        raise error

    passing = UseFramework("passes", log_after_execution=False)
    passing.add_feature_error_handler(watches_and_passes_it_on)

    @passing.feature(BrokenCommand)
    class BreaksTwo(Feature):
        def execute(self, dto: BrokenCommand) -> Answer:
            raise Boom("it broke")

    with pytest.raises(Boom):
        passing(BrokenCommand())
    assert seen == ["it broke", "it broke"]
