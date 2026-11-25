"""`CachedTokenSource`: a token source whose token comes from a token endpoint and is cached in a
`TokenCache` — what `ClientCredentials` and `TokenExchange` are, and what a project's own grant
(a JWT bearer assertion, a SAML one) is by saying two things: what the token is cached by, and how
it is fetched.

    class JwtBearerGrant(CachedTokenSource):
        def _cache_key(self, request):
            return ("jwt_bearer", self.endpoint.url, self.client.client_id, self.scope)

        def _fetch(self, request):
            return self._request_token([("grant_type", JWT_BEARER), ("assertion", sign())])

Context: the headers are `Bearer`, or `DPoP` with this request's proof for a token bound to the
source's `DpopProver`; a 401 bringing a new `DPoP-Nonce` is worth asking again; else the token
refused is dropped from the cache (`TokenCache.invalidate`) and a new one fetched once.
"""

from abc import abstractmethod
from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.client.adapters.token_cache import TokenCache
from sincpro_framework.auth.client.adapters.token_endpoint import TokenEndpointMixin
from sincpro_framework.auth.client.domain.outbound import (
    OutgoingRequest,
    TokenSource,
    parse_bearer_token,
)
from sincpro_framework.auth.client.domain.token_response import TokenResponse


class CachedTokenSource(TokenEndpointMixin, TokenSource):
    """A token from a token endpoint, cached by `_cache_key` and fetched by `_fetch`."""

    cache: TokenCache

    @abstractmethod
    def _cache_key(self, request: OutgoingRequest) -> tuple[Any, ...]:
        """What the token for `request` is cached by — the grant's parameters, never a secret
        in clear."""

    @abstractmethod
    def _fetch(self, request: OutgoingRequest) -> TokenResponse:
        """A new token for `request` from the endpoint."""

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """The cached token — fetched when there is none still valid — as `Bearer`, or `DPoP`
        with this request's proof."""
        issued = self.cache.token(self._cache_key(request), lambda: self._fetch(request))
        return self._authorization_headers(
            request, issued.access_token.get_secret_value(), issued.token_type
        )

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """Whether asking again may succeed: a 401 bringing a new DPoP nonce — now stored; or
        the token refused dropped from the cache (`TokenCache.invalidate`)."""
        if self.dpop is not None and answer and self.dpop.update_nonce(request.url, answer):
            return True
        token = parse_bearer_token(presented)
        return token is not None and self.cache.invalidate(
            self._cache_key(request), token, lambda: self._fetch(request)
        )
