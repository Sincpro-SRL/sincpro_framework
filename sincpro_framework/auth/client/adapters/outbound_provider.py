"""`OutboundCredentialsProvider`: what a call to another service of this platform carries, per
service — a token exchanged for the caller, this service's own client credentials — beside the
providers that authenticate who calls in.

    auth = AccessControl(providers=[
        jwt,
        OutboundCredentialsProvider({"orders": TokenExchange("orders-api", client=client,
                                                             issuer=…)}),
    ])
    security.identity_headers("orders")   # what remote_execution sends: the exchanged token

Context: it authenticates nobody (`None`); `outbound_for(identity, audience)` answers the headers
of the source named for that audience — `None` for a call that names no audience, or one it has
no source for — so `remote_execution`, which asks `security.identity_headers(context)`, carries
them with no change to its transports.
"""

from collections.abc import Mapping

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider


class OutboundCredentialsProvider(AuthProvider):
    """The headers of `sources[audience]` for a call to `audience`."""

    def __init__(
        self, sources: Mapping[str, TokenSource], name: str = "outbound-credentials"
    ) -> None:
        self.sources = dict(sources)
        self.name = name

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Never — it presents credentials, it does not authenticate."""
        return None

    def outbound_for(
        self, identity: Identity, audience: str | None = None
    ) -> Credentials | None:
        """The headers of the source for `audience` — `None` with no audience, or for one it
        does not name."""
        source = None if audience is None else self.sources.get(audience)
        if source is None:
            return None
        headers = source.headers_for(
            OutgoingRequest(
                url=f"sincpro://{audience}", identity=identity, tenant=identity.tenant
            )
        )
        return Credentials(transport="service", headers=headers)
