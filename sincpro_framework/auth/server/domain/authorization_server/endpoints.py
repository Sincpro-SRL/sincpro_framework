"""The endpoints an authorization server serves — each one a capability composed — and the name
the metadata document gives its URL (RFC 8414 §2)."""

from enum import StrEnum


class AuthorizationServerEndpoint(StrEnum):
    """An endpoint of the authorization server, by the name its URL is given to `metadata`."""

    AUTHORIZATION = "authorization"
    TOKEN = "token"
    REGISTRATION = "registration"
    REVOCATION = "revocation"
    JWKS = "jwks"

    @property
    def metadata_key(self) -> str:
        """The member of the metadata document its URL goes in — `token_endpoint`, `jwks_uri`."""
        return (
            "jwks_uri"
            if self is AuthorizationServerEndpoint.JWKS
            else f"{self.value}_endpoint"
        )
