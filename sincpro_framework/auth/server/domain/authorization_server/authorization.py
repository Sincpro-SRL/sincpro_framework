"""The authorization code grant as data: what a client asks for at `/authorize`, the
authorization waiting for the person to sign in, and the code handed back.

Theory — the authorization code grant (RFC 6749 §4.1), the flow every OAuth client with a
browser uses:

    client ──/authorize──▶ server       an AuthorizationRequest: who asks, where to answer
    person ──signs in────▶ project      the project's own login page, never the framework's
    server ──redirect────▶ client       ?code=…&state=…&iss=…  — a code, not a token
    client ──/token──────▶ server       the code + its PKCE verifier  ──▶  tokens

The code travels through the browser, where it may leak; it is worth nothing without the PKCE
verifier only the client holds (RFC 7636): the client sends `code_challenge`, derived from a
secret `code_verifier`, at `/authorize`, and the verifier itself at `/token`. `state` is the
client's own value, returned as it came, so it recognizes its answer; `iss` names the server
that answers (RFC 9207), so a client talking to several never mixes them up.

What a client may ask for is checked once its client and redirect are verified; a request refused
after that goes back to the client's redirect with the error (RFC 6749 §4.1.2.1), never to an
error page of the server's.

How strict PKCE is, is the server's choice (`PkcePolicy`): required of every client by default —
OAuth 2.1 — or of public clients only, as RFC 6749 + 7636 had it, or optional. `S256` is the
method accepted by default; `plain` (the verifier as the challenge) only where a server allows it.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.domain.identity import Assurance, Identity
from sincpro_framework.sincpro_abstractions import DataTransferObject


class CodeChallengeMethod(StrEnum):
    """How a PKCE challenge is derived from its verifier — RFC 7636 §4.2."""

    S256 = "S256"
    """BASE64URL(SHA-256(verifier)): a code that leaks with its challenge is still worth nothing."""
    PLAIN = "plain"
    """The verifier itself — only for a client that cannot hash; OAuth 2.1 drops it."""


class PkcePolicy(StrEnum):
    """Which clients must prove their requests with PKCE."""

    REQUIRED = "required"
    """Every client — OAuth 2.1, the default."""
    PUBLIC_CLIENTS = "public_clients"
    """Clients with no secret; a confidential client proves itself by its secret — RFC 6749 + 7636."""
    OPTIONAL = "optional"
    """Verified when a challenge was sent, never asked for."""


class AuthorizationRequest(DataTransferObject):
    """What a client asked for at `/authorize`."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    redirect_uri: str
    response_type: str = "code"
    """`code` — the only one OAuth 2.1 retains; any other is `unsupported_response_type`."""
    code_challenge: str = ""
    """Empty when the client sent none — what the server's `PkcePolicy` decides about."""
    code_challenge_method: CodeChallengeMethod = CodeChallengeMethod.S256
    scopes: frozenset[str] = frozenset()
    state: str = ""
    resource: str | None = None
    """The resource the token is for — RFC 8707."""
    tenant: str | None = None
    """The tenant the authorization is for — what the transport knows (the host, the path, a
    parameter); a client bound to a tenant sets it when the request does not (#339)."""

    @classmethod
    def from_query(
        cls, parameters: Mapping[str, str], tenant: str | None = None
    ) -> "AuthorizationRequest":
        """The request a client sent to `/authorize`, from its query, as the RFCs read it — what
        a transport builds it with (#340).

            AuthorizationRequest.from_query(dict(request.query_params), tenant=tenant_of(host))

        1. `client_id` and `redirect_uri` are required — `invalid_request`.
        2. `code_challenge_method` omitted with a challenge is `plain` (RFC 7636 §4.3), which the
           server refuses unless it accepts plain; an unknown one is `invalid_request`.
        Final: `scope` split on spaces; every other parameter as it came.
        """
        missing = [name for name in ("client_id", "redirect_uri") if not parameters.get(name)]
        if missing:
            raise OAuthError(OAuthErrorCode.INVALID_REQUEST, f"{missing[0]} is required")
        challenge = parameters.get("code_challenge", "")
        named = parameters.get("code_challenge_method")
        try:
            method = (
                CodeChallengeMethod(named)
                if named
                else (CodeChallengeMethod.PLAIN if challenge else CodeChallengeMethod.S256)
            )
        except ValueError as unknown:
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST, f"code_challenge_method {named!r} is unknown"
            ) from unknown
        return cls(
            client_id=parameters["client_id"],
            redirect_uri=parameters["redirect_uri"],
            response_type=parameters.get("response_type", "code"),
            code_challenge=challenge,
            code_challenge_method=method,
            scopes=frozenset(parameters.get("scope", "").split()),
            state=parameters.get("state", ""),
            resource=parameters.get("resource") or None,
            tenant=tenant,
        )

    def build_redirect_uri(self, parameters: Mapping[str, str]) -> str:
        """Its `redirect_uri` with `parameters` added to the query it already had; an empty value
        is left out.

            build_redirect_uri({"code": "c-1", "state": "xyz"})
                ->  "https://app/cb?code=c-1&state=xyz"
        """
        parts = urlsplit(self.redirect_uri)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query += [(key, value) for key, value in parameters.items() if value]
        return urlunsplit(parts._replace(query=urlencode(query)))


class PendingAuthorization(DataTransferObject):
    """An authorization waiting for the person to sign in — what the project's login page shows,
    by its `id`, and completes or denies."""

    model_config = ConfigDict(frozen=True)

    id: str
    request: AuthorizationRequest
    client_name: str = ""


class AuthorizationCode(DataTransferObject):
    """What a code stands for: the request it answers, and who signed in and how."""

    model_config = ConfigDict(frozen=True)

    request: AuthorizationRequest
    identity: Identity
    assurance: Assurance = Assurance()

    def verify_pkce(self, code_verifier: str | None) -> bool:
        """Whether `code_verifier` proves the request — RFC 7636 §4.6, in constant time.

        no challenge was sent            ->  True when no verifier comes either
        S256                             ->  BASE64URL(SHA-256(verifier)) is the challenge
        plain                            ->  the verifier is the challenge
        """
        challenge = self.request.code_challenge
        if not challenge:
            return not code_verifier
        if not code_verifier:
            return False
        if self.request.code_challenge_method is CodeChallengeMethod.PLAIN:
            computed = code_verifier
        else:
            hashed = hashlib.sha256(code_verifier.encode("ascii", "replace")).digest()
            computed = base64.urlsafe_b64encode(hashed).rstrip(b"=").decode()
        return hmac.compare_digest(computed, challenge)
