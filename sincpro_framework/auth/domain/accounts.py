"""What a service that manages its own users needs to say about them — never a user table: the
project keeps its accounts as entities of its own, and asks these pieces to hash, lock, link and
issue.

    class Accounts(CredentialVerifier):            the project's own: its table, its rules
        def verify(self, username: str, password: str) -> Identity | None: ...
"""

from abc import ABC, abstractmethod

from sincpro_framework.auth.domain.identity import Identity


class CredentialVerifier(ABC):
    """The port: who a username and a password belong to — `None` when either is wrong, and the
    same `None` whichever was, so nobody learns which usernames exist."""

    @abstractmethod
    def verify(self, username: str, password: str) -> Identity | None:
        """The identity the pair proves, or `None`."""
