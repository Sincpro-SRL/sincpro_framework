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
