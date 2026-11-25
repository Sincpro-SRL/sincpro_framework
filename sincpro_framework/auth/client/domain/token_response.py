"""What a token endpoint answers, in auth's words — the token, its type, when it expires — and the
one rule every cached or stored token is renewed by.

    TokenResponse(access_token=SecretStr("eyJ…"), token_type="Bearer",
                  received_at=t, expires_at=t + timedelta(minutes=5))
    token_renewal_time(t, t + timedelta(minutes=5), skew=timedelta(seconds=30))   ->  t + 4m30s
    token_renewal_time(t, t + timedelta(seconds=20), skew=timedelta(seconds=30))  ->  t + 10s

Context: a token is used until `skew` before it expires, the skew at most half its life — so a
token that lives less than twice the skew is still used for half of it, never fetched on every
call. `TokenCache` and `RefreshTokenSource` both judge a token by `token_renewal_time`.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, SecretStr

from sincpro_framework.sincpro_abstractions import DataTransferObject


class TokenType(StrEnum):
    """The `token_type` a token endpoint may answer that this client can present — RFC 6749
    §7.1, RFC 9449 (`DPoP`), RFC 8693 §2.2.1 (`N_A`, a token that is not an access token).
    Compared lower-cased: servers answer `Bearer`, `bearer` or `DPoP`."""

    BEARER = "bearer"
    DPOP = "dpop"
    N_A = "n_a"


class TokenTypeIdentifier(StrEnum):
    """The URIs that name a token's type in a token exchange — RFC 8693 §3: what the subject
    or actor token is, and what is requested."""

    ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
    REFRESH_TOKEN = "urn:ietf:params:oauth:token-type:refresh_token"
    ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
    JWT = "urn:ietf:params:oauth:token-type:jwt"
    SAML1 = "urn:ietf:params:oauth:token-type:saml1"
    SAML2 = "urn:ietf:params:oauth:token-type:saml2"


def parse_seconds(value: Any) -> float | None:
    """A positive number of seconds out of a JSON answer — a number or a numeric string
    (`"3599"`, which some servers send); `None` for anything else.

        parse_seconds(3599)    ->  3599.0
        parse_seconds("3599")  ->  3599.0
        parse_seconds(True)    ->  None
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if 0 < seconds < float("inf") else None


def token_renewal_time(
    received_at: datetime | None, expires_at: datetime, skew: timedelta
) -> datetime:
    """When a token stops being used: `skew` before it expires, at most half its life — the
    whole `skew` when it is not known when the token arrived."""
    if received_at is None:
        return expires_at - skew
    lifetime = max(expires_at - received_at, timedelta(0))
    return expires_at - min(skew, lifetime / 2)


class TokenResponse(DataTransferObject):
    """What a token endpoint answered (RFC 6749 §5.1) — its secrets as `SecretStr`, shown by no
    `repr`."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    token_type: str = "Bearer"
    expires_at: datetime
    received_at: datetime
    refresh_token: SecretStr | None = None
    scope: frozenset[str] = Field(default_factory=frozenset)
    issued_token_type: str | None = None
