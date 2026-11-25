"""`AuthorizationServerMetadata`: what the server says about itself — RFC 8414, read by a client
before it asks for anything — built from what the capabilities composed say they serve, so it
never publishes an endpoint, a grant or a method it does not serve."""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)


class AuthorizationServerMetadata(AuthorizationServerCore):
    """The authorization server metadata document."""

    def metadata(self, endpoint_urls: Mapping[str, str]) -> dict[str, Any]:
        """The document, with the URL the transport serves each endpoint at — by
        `AuthorizationServerEndpoint` (`authorization`, `token`, `registration`, `revocation`,
        `jwks`) — only those this server serves; a URL given for another is left out.

            server.metadata({"authorization": f"{base}/authorize", "token": f"{base}/token"})
        """
        document = self.capabilities()
        for endpoint in self.served_endpoints():
            if endpoint.value in endpoint_urls:
                document[endpoint.metadata_key] = endpoint_urls[endpoint.value]
        return document
