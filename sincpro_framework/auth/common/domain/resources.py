"""The resources an authorization server issues tokens for — RFC 8707 resource indicators — and
the audiences the verifier of its own tokens accepts: one decision, read on both sides (#335).

    Resources("https://mcp.sincpro.com.bo/mcp")
        audience_for("https://MCP.sincpro.com.bo:443/mcp/", client, tenant)
            ->  "https://mcp.sincpro.com.bo/mcp"            the canonical form, the token's `aud`
        audience_for("https://evil.example/api", client, tenant)   ->  None: invalid_target
        serves("https://mcp.sincpro.com.bo/mcp")                    ->  True

Theory — an MCP client (and any OAuth 2.1 client that talks to several APIs) names the API it
wants a token for in `resource`; the server issues a token whose `aud` is that API, so a token
stolen from one API is refused by another. The server must refuse a resource it does not serve
(`invalid_target`, RFC 8707 §2), and the API must accept the audience the server issued — when
the two disagree, every sign-in ends in a token nobody accepts.

Extend: a `ResourcePolicy` of the project's decides with the client and the tenant the request
is for — one resource per tenant (`https://{tenant}.mcp.sincpro.com.bo/mcp`), resources a client
may not ask for — without listing them:

    class TenantResources(ResourcePolicy):
        def audience_for(self, resource, client_id, tenant):
            canonical = canonical_resource(resource)
            return canonical if canonical == f"https://{tenant}.mcp.sincpro.com.bo/mcp" else None

        def serves(self, audience):
            return TENANT_MCP.fullmatch(audience) is not None
"""

from abc import ABC, abstractmethod
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = {"https": 443, "http": 80}


def canonical_resource(uri: str) -> str:
    """`uri` in the one form a token's `aud` carries: scheme and host lower-case, the default
    port dropped, no trailing slash, no fragment — the spellings a client may send of one
    resource compare equal.

        canonical_resource("HTTPS://MCP.sincpro.com.bo:443/mcp/")  ->  "https://mcp.sincpro.com.bo/mcp"
    """
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = (
        host if parts.port in (None, DEFAULT_PORTS.get(scheme)) else f"{host}:{parts.port}"
    )
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, ""))


class ResourcePolicy(ABC):
    """The port: which resources tokens are issued for, and which audiences are served."""

    @abstractmethod
    def audience_for(self, resource: str, client_id: str, tenant: str | None) -> str | None:
        """The audience a token asked for `resource` by `client_id`, for `tenant`, is issued
        for — `None` when this server does not serve it (`invalid_target`)."""

    @abstractmethod
    def serves(self, audience: str) -> bool:
        """Whether a token whose `aud` is `audience` is one of the resources served — what the
        verifier of the issuer's own tokens accepts besides its `audience`."""


class Resources(ResourcePolicy):
    """A fixed list of resources, compared in canonical form."""

    def __init__(self, *uris: str) -> None:
        """`uris` — absolute URIs, as a client names them in `resource`."""
        self.uris = frozenset(canonical_resource(one) for one in uris)

    def audience_for(self, resource: str, client_id: str, tenant: str | None) -> str | None:
        """The canonical form of `resource` when it is one of the list — for every client and
        tenant."""
        canonical = canonical_resource(resource)
        return canonical if canonical in self.uris else None

    def serves(self, audience: str) -> bool:
        return canonical_resource(audience) in self.uris
