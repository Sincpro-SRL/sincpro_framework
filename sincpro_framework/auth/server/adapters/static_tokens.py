"""`StaticProvider`: identities known in advance, by the bearer token that stands for each — for
tests of an entrypoint, a local environment, a script with a fixed token.

    StaticProvider({"token-a": Identity.user("user:1", permissions={...})})

Context: a request with no bearer is not its kind — `None`, the next provider is tried; a bearer
it does not know is refused, as a real issuer would refuse a token it never signed.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.domain.provider import AuthProvider


class StaticProvider(AuthProvider):
    """An `AuthProvider` over a fixed table of bearer token to identity."""

    def __init__(self, identities: Mapping[str, Identity], name: str = "static") -> None:
        """The table, copied — a change to the mapping given afterwards is not seen."""
        self.identities = dict(identities)
        self.name = name

    def security_scheme(self) -> dict[str, Any] | None:
        """HTTP bearer, as the tokens it knows are sent."""
        return {"type": "http", "scheme": "bearer"}

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The identity stored for the bearer, stamped with this provider's name.

            provider = StaticProvider({"token-a": Identity.user("user:1")})
            provider.authenticate(Credentials(transport="http", headers={
                "authorization": "Bearer token-a"}))
                ->  Identity(subject="user:1", provider="static", …)

        No bearer is `None`; a bearer not in the table is `Unauthenticated("unknown token")`.
        """
        token = credentials.bearer
        if token is None:
            return None
        identity = self.identities.get(token)
        if identity is None:
            raise exceptions.Unauthenticated("unknown token")
        return identity.model_copy(update={"provider": self.name})
