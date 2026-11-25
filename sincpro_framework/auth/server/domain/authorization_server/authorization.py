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
    code_challenge: str = ""
    """Empty when the client sent none — what the server's `PkcePolicy` decides about."""
    code_challenge_method: CodeChallengeMethod = CodeChallengeMethod.S256
    scopes: frozenset[str] = frozenset()
    state: str = ""
    resource: str | None = None
    """The resource the token is for — RFC 8707."""

    def redirect_with(self, parameters: Mapping[str, str]) -> str:
        """Its `redirect_uri` with `parameters` added to the query it already had; an empty value
        is left out.

            redirect_with({"code": "c-1", "state": "xyz"})  ->  "https://app/cb?code=c-1&state=xyz"
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

    def verifies(self, code_verifier: str | None) -> bool:
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
