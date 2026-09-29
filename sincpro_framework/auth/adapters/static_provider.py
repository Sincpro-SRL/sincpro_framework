"""`StaticProvider`: identities known in advance, by the bearer token that stands for each — for
tests of an entrypoint, a local environment, a script with a fixed token.

    StaticProvider({"token-a": Identity.user("user:1", permissions={...})})

Context: a request with no bearer is not its kind — `None`, the next provider is tried; a bearer
it does not know is refused, as a real issuer would refuse a token it never signed.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.domain import AuthProvider, Credentials, Identity, Unauthenticated


class StaticProvider(AuthProvider):
    def __init__(self, identities: Mapping[str, Identity], name: str = "static") -> None:
        self.identities = dict(identities)
        self.name = name

    def security_scheme(self) -> dict[str, Any] | None:
        return {"type": "http", "scheme": "bearer"}

    def authenticate(self, credentials: Credentials) -> Identity | None:
        token = credentials.bearer
        if token is None:
            return None
        identity = self.identities.get(token)
        if identity is None:
            raise Unauthenticated("unknown token")
        return identity.model_copy(update={"provider": self.name})
