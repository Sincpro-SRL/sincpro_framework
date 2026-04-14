"""End-to-end error handler tests using UseFramework."""

from sincpro_framework import DataTransferObject, Feature, UseFramework


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
