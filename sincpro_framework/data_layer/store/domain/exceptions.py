"""What the store raises: the key-value store that did not answer, and the leases built on one."""

from sincpro_framework.exceptions import (
    ClientError,
    FailureKind,
    ProgrammingError,
    ServiceUnavailableError,
)


class StoreUnavailable(ServiceUnavailableError):
    """The store did not answer — down, unreachable, past its timeout. Whoever saves context,
    drafts or cached values there retries later; the cache answers from the process meanwhile.
    """


class LeaseTaken(ClientError):
    """Somebody else holds the lease — the same work running elsewhere. Worth trying again once
    it is released or its time passes."""

    failure_kind = FailureKind.IN_PROGRESS

    def __init__(self, name: str, holder: str) -> None:
        """`holder` is who holds it now, as the lease recorded — empty when unknown."""
        super().__init__(f"{name} is held by {holder or 'another holder'}")
        self.name = name
        self.holder = holder


class LeaseLost(ClientError):
    """The holder no longer holds the lease: its time passed before it was renewed, and somebody
    else may have taken it. What the holder does next is no longer protected by it."""

    failure_kind = FailureKind.CONFLICT

    def __init__(self, name: str, owner: str) -> None:
        """`owner` is the holder that lost it — empty when unknown."""
        super().__init__(f"{owner or 'the holder'} no longer holds {name}")
        self.name = name
        self.owner = owner
