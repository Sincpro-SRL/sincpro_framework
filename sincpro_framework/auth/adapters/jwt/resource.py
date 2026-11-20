"""`ProtectedResource`: what this service says about itself as an OAuth protected resource —
RFC 9728 — so an MCP client, or any OAuth client, finds where to get a token for it.

    resource = ProtectedResource("https://billing.acme.bo/mcp",
                                 authorization_servers=["https://auth.acme.bo/realms/acme"])
    resource.metadata_url   ->  "https://billing.acme.bo/.well-known/oauth-protected-resource/mcp"
    resource.metadata()     ->  {"resource": "...", "authorization_servers": [...], ...}

Context: a 401 names the metadata URL in its `WWW-Authenticate` (`resource_metadata=`); the
document is served by the entrypoint — MCP's already does.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ProtectedResource:
    """The resource's identifier and the authorization servers that issue tokens for it."""

    resource: str
    authorization_servers: Sequence[str]
    scopes_supported: Sequence[str] = ()
    bearer_methods_supported: Sequence[str] = ("header",)
    resource_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata_url(self) -> str:
        """Where the metadata is published: the well-known part between the host and the
        resource's path (RFC 9728 §3.1)."""
        parts = urlsplit(self.resource)
        path = parts.path.rstrip("/")
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                f"/.well-known/oauth-protected-resource{path}",
                "",
                "",
            )
        )

    def metadata(self) -> dict[str, Any]:
        """The RFC 9728 document."""
        document: dict[str, Any] = {
            "resource": self.resource,
            "authorization_servers": list(self.authorization_servers),
            "bearer_methods_supported": list(self.bearer_methods_supported),
        }
        if self.scopes_supported:
            document["scopes_supported"] = list(self.scopes_supported)
        if self.resource_name:
            document["resource_name"] = self.resource_name
        return {**document, **self.extra}
