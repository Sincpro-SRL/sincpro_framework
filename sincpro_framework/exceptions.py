class DTOAlreadyRegistered(Exception):
    pass


class DependencyAlreadyRegistered(Exception):
    pass


class DependencyNotRegistered(AttributeError):
    """Raised when a dependency is accessed but was never registered."""


class UnknownDTOToExecute(Exception):
    pass


class SincproFrameworkNotBuilt(Exception):
    pass


class BusAlreadyBuilt(Exception):
    """Something was registered on a bus that is already built, where it would never run."""


class InterceptorContractViolation(TypeError):
    """An interceptor changed the class of the Command it passed on, or of the response."""


class ExtensionRefused(Exception):
    """A way of extending the framework that cannot work as declared — a circle of `before` and
    `after`, `extends=` on a class that is not a subclass — refused where it is declared.
    Anything that works, only not as expected, is a warning instead."""


class ContextRequired(Exception):
    """A use case declared context keys it cannot run without (`requires_context`), and the context
    of this execution does not have them. A use case that declared nothing is never checked.
    """

    def __init__(self, use_case: str, missing: list[str]) -> None:
        super().__init__(
            f"{use_case} requires {', '.join(missing)} in the context — open it with "
            f"bus.context({{...}}), or register a context_provider that gives them"
        )
        self.use_case = use_case
        self.missing = missing
