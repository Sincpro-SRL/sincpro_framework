"""`TokenEndpoint`: the one place a grant is POSTed to an authorization server — client
credentials, a refresh, an exchange, a device code — and its answer read the same way.

    endpoint = TokenEndpoint("https://auth.bank.bo/token", ClientSecretBasic("billing", secret))
    endpoint.grant([("grant_type", "client_credentials"), ("scope", "payments")])
        ->  IssuedToken(token_type="Bearer", expires_at=…, …)        never shown: the token

Context: what the server answers is said in auth's words — a server down, overloaded (429) or
`temporarily_unavailable` is `EndpointUnavailable` (503, with its `Retry-After`); `invalid_grant`
is `GrantRejected`; anything else it refuses is `CredentialsRejected` — this service's
configuration, never the caller's. The expiry is the moment the answer arrived plus
`expires_in`, never a JWT's `exp` read against this clock; with no `expires_in`, five minutes —
never forever.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial

from pydantic import ConfigDict, Field, SecretStr

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient, HttpRequest
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

ACCEPTED_TYPES = frozenset({"bearer", "dpop", "n_a"})


class IssuedToken(DataTransferObject):
    """What a token endpoint issued — its secrets as `SecretStr`, shown by no `repr`."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    token_type: str = "Bearer"
    expires_at: datetime
    received_at: datetime
    refresh_token: SecretStr | None = None
    scope: frozenset[str] = Field(default_factory=frozenset)
    issued_token_type: str | None = None


class TokenEndpoint:
    """Grants POSTed to `endpoint` as `client`, through `http`."""

    def __init__(
        self,
        endpoint: str | Callable[[], str],
        client: ClientAuthentication,
        http: HttpClient,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        assumed_lifetime: timedelta = timedelta(minutes=5),
        timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        """`endpoint` is the URL — or a function answering it, asked on the first grant."""
        self._endpoint = endpoint
        self.client = client
        self.http = http
        self.now = now
        self.assumed_lifetime = assumed_lifetime
        self.timeout = timeout

    @property
    def url(self) -> str:
        """The token endpoint's URL."""
        return self._endpoint() if callable(self._endpoint) else self._endpoint

    def grant(self, fields: Sequence[tuple[str, str]]) -> IssuedToken:
        """The token the server issues for `fields`.

        1. POSTed form-encoded, authenticated as the client.
        2. 5xx, 429, or `temporarily_unavailable`: `EndpointUnavailable` with its Retry-After.
        3. `invalid_grant`: `GrantRejected`; any other refusal, or an answer without an
           `access_token` of a known type: `CredentialsRejected`.
        Final: the token, expiring `expires_in` after it arrived — five minutes when not said.
        """
        url = self.url
        target = HttpRequest(url=url).target
        sent, headers = self.client.applied(fields, url)
        answer = self.http.send(HttpRequest.form(url, sent, headers, self.timeout))
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
            refused = (
                exceptions.GrantRejected
                if error == "invalid_grant"
                else exceptions.CredentialsRejected
            )
            if error not in ("authorization_pending", "slow_down"):
                logger.warning(
                    "a token endpoint refused this service",
                    endpoint=target,
                    error=error or str(answer.status),
                )
            raise refused(
                target,
                error or f"status {answer.status}",
                str(said.get("error_description", "")),
            )
        token = said.get("access_token")
        token_type = str(said.get("token_type", "Bearer"))
        if (
            not isinstance(token, str)
            or not token
            or token_type.lower() not in ACCEPTED_TYPES
        ):
            raise exceptions.CredentialsRejected(
                target, "malformed_answer", "no usable access_token"
            )
        lifetime = said.get("expires_in")
        alive = (
            timedelta(seconds=lifetime)
            if isinstance(lifetime, (int, float))
            and not isinstance(lifetime, bool)
            and lifetime > 0
            else self.assumed_lifetime
        )
        refresh = said.get("refresh_token")
        scope = said.get("scope")
        return IssuedToken(
            access_token=SecretStr(token),
            token_type=token_type,
            expires_at=received + alive,
            received_at=received,
            refresh_token=(
                SecretStr(refresh) if isinstance(refresh, str) and refresh else None
            ),
            scope=frozenset(scope.split()) if isinstance(scope, str) else frozenset(),
            issued_token_type=said.get("issued_token_type"),
        )
