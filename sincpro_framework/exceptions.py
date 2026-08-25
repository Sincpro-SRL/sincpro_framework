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
