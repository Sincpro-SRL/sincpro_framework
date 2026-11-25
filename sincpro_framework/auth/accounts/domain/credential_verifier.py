"""Who a username and a password belong to — the port a project's own accounts implement: the
framework never stores a user table; the project stores its accounts as entities of its own, and
`BasicProvider` or a sign-in use case asks them.

    class Accounts(CredentialVerifier):            the project's own: its table, its rules
        def verify(self, username: str, password: str) -> Identity | None: ...
"""

from abc import ABC, abstractmethod

from sincpro_framework.auth.common.domain.identity import Identity


class CredentialVerifier(ABC):
    """The port: who a username and a password belong to — `None` when either is wrong, and the
    same `None` whichever was, so nobody learns which usernames exist."""

    @abstractmethod
    def verify(self, username: str, password: str) -> Identity | None:
        """The identity the pair proves, or `None`."""
