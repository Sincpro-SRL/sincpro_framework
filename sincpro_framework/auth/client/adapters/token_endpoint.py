"""`TokenEndpoint`: the one place a grant is POSTed to an authorization server — client
credentials, a refresh, an exchange, a device code — and its answer read the same way;
`TokenEndpointMixin`: what every token source that asks one sets up the same way.

    endpoint = TokenEndpoint("https://auth.bank.bo/token", ClientSecretBasic("billing", secret))
    endpoint.request_token([("grant_type", "client_credentials"), ("scope", "payments")])
        ->  TokenResponse(token_type="Bearer", expires_at=…, …)        never shown: the token

Context: what the server answers is said in auth's words — a server down, overloaded (429) or
`temporarily_unavailable` is `EndpointUnavailable` (503, with its `Retry-After`); `invalid_grant`
is `GrantRejected`; anything else it refuses is `CredentialsRejected` — this service's
configuration, never the caller's. The expiry is the moment the answer arrived plus
`expires_in` (a number, or a numeric string), never a JWT's `exp` read against this clock; with no
`expires_in`, `default_expires_in` — never forever. Given a `DpopProver`, the request carries a
proof, and a server demanding a nonce (`use_dpop_nonce`, RFC 9449 §8) is asked once more with it.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from sincpro_framework.auth.client.adapters.client_authentication import (
    ClientSecretBasic,
    PublicClient,
)
from sincpro_framework.auth.client.domain.outbound import OutgoingRequest
from sincpro_framework.auth.client.domain.token_response import (
    TokenResponse,
    TokenType,
    parse_seconds,
)
from sincpro_framework.auth.common.adapters.server_metadata import (
    OidcDiscovery,
    resolve_discovery,
    resolve_endpoint,
)
from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import (
    ClientAuthentication,
    HttpClient,
    HttpRequest,
)
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver

QUIET_ERRORS = frozenset({"authorization_pending", "slow_down", "use_dpop_nonce"})
"""Refusals that are part of a flow — polled again, or asked once more — never warned about."""


def resolve_client(
    client: ClientAuthentication | None, client_id: str | None, client_secret: Any
) -> ClientAuthentication:
    """The client authentication given; else `ClientSecretBasic` of the id and the secret, or
    `PublicClient` of an id with no secret — `ProgrammingError` when there is not even an id.
    """
    if client is not None:
        return client
    if client_id is None:
        raise exceptions.ProgrammingError("give client=, or client_id= (and client_secret=)")
    if client_secret is None:
        return PublicClient(client_id)
    return ClientSecretBasic(client_id, client_secret)


class TokenEndpoint:
    """Grants POSTed to `endpoint` as `client`, through `http`."""

    def __init__(
        self,
        endpoint: str | Callable[[], str],
        client: ClientAuthentication,
        http: HttpClient,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        default_expires_in: timedelta = timedelta(minutes=5),
        timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        """`endpoint` is the URL — or a function answering it, asked on the first grant."""
        self._endpoint = endpoint
        self.client = client
        self.http = http
        self.now = now
        self.default_expires_in = default_expires_in
        self.timeout = timeout

    @property
    def url(self) -> str:
        """The token endpoint's URL."""
        return self._endpoint() if callable(self._endpoint) else self._endpoint

    def _post(
        self, fields: Sequence[tuple[str, str]], headers: Mapping[str, str]
    ) -> TokenResponse:
        """The token the server issues for `fields`, sent with `headers`.

        1. POSTed form-encoded, authenticated as the client.
        2. 5xx, 429, or `temporarily_unavailable`: `EndpointUnavailable` with its Retry-After.
        3. `invalid_grant`: `GrantRejected`; any other refusal, or an answer without an
           `access_token` of a known type: `CredentialsRejected` — carrying the `DPoP-Nonce`
           the server demands, when it sent one.
        Final: the token, expiring `expires_in` after it arrived — `default_expires_in` when
        not said.
        """
        url = self.url
        target = HttpRequest(url=url).target
        sent, authenticated = self.client.apply(fields, url)
        answer = self.http.send(
            HttpRequest.form(url, sent, {**authenticated, **headers}, self.timeout)
        )
        received = self.now()
        said = answer.json_object()
        error = str(said.get("error", ""))
        if answer.status >= 500 or answer.status == 429 or error == "temporarily_unavailable":
            raise exceptions.EndpointUnavailable(
                target,
                f"answered {answer.status}",
                answer.status,
                answer.retry_after(received),
            )
        if not answer.ok:
            refusal = (
                exceptions.GrantRejected
                if error == exceptions.OAuthErrorCode.INVALID_GRANT
                else exceptions.CredentialsRejected
            )
            if error not in QUIET_ERRORS:
                logger.warning(
                    "a token endpoint refused this service",
                    endpoint=target,
                    error=error or str(answer.status),
                )
            raise refusal(
                target,
                error or f"status {answer.status}",
                str(said.get("error_description", "")),
                answer.headers.get("dpop-nonce"),
            )
        token = said.get("access_token")
        token_type = str(said.get("token_type", "Bearer"))
        if not isinstance(token, str) or not token or token_type.lower() not in TokenType:
            raise exceptions.CredentialsRejected(
                target, "malformed_answer", "no usable access_token"
            )
        seconds = parse_seconds(said.get("expires_in"))
        refresh = said.get("refresh_token")
        scope = said.get("scope")
        return TokenResponse(
            access_token=SecretStr(token),
            token_type=token_type,
            expires_at=received
            + (self.default_expires_in if seconds is None else timedelta(seconds=seconds)),
            received_at=received,
            refresh_token=(
                SecretStr(refresh) if isinstance(refresh, str) and refresh else None
            ),
            scope=frozenset(scope.split()) if isinstance(scope, str) else frozenset(),
            issued_token_type=said.get("issued_token_type"),
        )

    def request_token(
        self,
        fields: Sequence[tuple[str, str]],
        headers: Mapping[str, str] | None = None,
        dpop: "DpopProver | None" = None,
    ) -> TokenResponse:
        """The token the server issues for `fields` — see the module's context.

        1. With `dpop`, a proof of its key for this POST is sent along.
        2. The server demands a DPoP nonce: stored, and asked once more with it.
        Final: the token issued.
        """
        if dpop is None:
            return self._post(fields, headers or {})
        url = self.url
        try:
            return self._post(
                fields, {**(headers or {}), "dpop": dpop.create_proof("POST", url)}
            )
        except exceptions.CredentialsRejected as refused:
            if refused.error != "use_dpop_nonce" or not refused.dpop_nonce:
                raise
            dpop.update_nonce(url, {"dpop-nonce": refused.dpop_nonce})
            return self._post(
                fields, {**(headers or {}), "dpop": dpop.create_proof("POST", url)}
            )


class TokenEndpointMixin:
    """What a token source that asks a token endpoint sets up the same way — the client, given or
    built of `client_id`/`client_secret`; the endpoint, given or discovered from `issuer`; the
    scope and resources (RFC 8707) every grant asks for; the `DpopProver` its tokens are bound
    to — and how the token is presented.

        class JwtBearerGrant(CachedTokenSource):
            def __init__(self, assertion, client=None, client_id=None, ...):
                self._init_token_endpoint(client, client_id, ...)
    """

    client: ClientAuthentication
    discovery: OidcDiscovery
    http: HttpClient
    endpoint: TokenEndpoint
    scope: tuple[str, ...]
    resources: tuple[str, ...]
    dpop: "DpopProver | None"

    def _init_token_endpoint(
        self,
        client: ClientAuthentication | None,
        client_id: str | None,
        client_secret: Any,
        token_endpoint: str | None,
        issuer: str | None,
        http: HttpClient | None,
        discovery: OidcDiscovery | None,
        now: Callable[[], datetime],
        timeout: timedelta,
        scope: Iterable[str] = (),
        resources: Iterable[str] = (),
        dpop: "DpopProver | None" = None,
    ) -> None:
        """The client and the token endpoint, set up once — see the class."""
        self.client = resolve_client(client, client_id, client_secret)
        self.discovery = resolve_discovery(http, discovery)
        self.http = http or UrllibHttp()
        self.endpoint = TokenEndpoint(
            resolve_endpoint(token_endpoint, issuer, self.discovery, "token_endpoint"),
            self.client,
            self.http,
            now,
            timeout=timeout,
        )
        self.scope = tuple(sorted(scope))
        self.resources = tuple(sorted(resources))
        self.dpop = dpop

    def _request_token(self, fields: Sequence[tuple[str, str]]) -> TokenResponse:
        """The token for `fields`, asking for the scope and the resources too — proved with the
        DPoP key when there is one."""
        asked = list(fields)
        if self.scope:
            asked.append(("scope", " ".join(self.scope)))
        asked.extend(("resource", one) for one in self.resources)
        return self.endpoint.request_token(asked, dpop=self.dpop)

    def _authorization_headers(
        self, request: OutgoingRequest, token: str, token_type: str
    ) -> dict[str, str]:
        """`Bearer <token>` — or, for a DPoP-bound token, `DPoP <token>` with this request's
        proof (RFC 9449)."""
        if self.dpop is not None and token_type.lower() == TokenType.DPOP:
            return {
                "authorization": f"DPoP {token}",
                "dpop": self.dpop.create_proof(request.method, request.url, token),
            }
        return {"authorization": f"Bearer {token}"}
