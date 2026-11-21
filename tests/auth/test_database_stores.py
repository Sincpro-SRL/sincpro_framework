"""What auth keeps, on a table of the project's database — the same contracts the stores in memory
and on Redis honour, racing on a file as replicas would on a server — and the token issuer whole
on them."""

import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData

from sincpro_framework.auth import (
    Credentials,
    Identity,
    KeyValueRevocations,
    Revocations,
    Unauthenticated,
)
from sincpro_framework.auth.accounts import RefreshTokens, SigningKeys, TokenIssuer
from sincpro_framework.auth.grants import GrantStore
from sincpro_framework.auth.server import ApiKeyStore
from sincpro_framework.data_layer.orm import (
    Database,
    DatabaseApiKeys,
    DatabaseGrants,
    DatabaseKeyValue,
    DatabaseRefreshTokens,
    template_table,
)
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.runtime.testing import (
    GrantStoreContract,
    KeyValueStoreContract,
    ManualClock,
    RevocationsContract,
)
from sincpro_framework.runtime.testing.contracts.api_key_store import ApiKeyStoreContract
from sincpro_framework.runtime.testing.contracts.refresh_tokens import RefreshTokensContract

metadata = MetaData()
key_values = template_table.key_value_table("auth_key_value", metadata)
api_keys = template_table.api_key_table("api_key", metadata)
assignments = template_table.grant_assignment_table("grant_assignment", metadata)
entitlements = template_table.entitlement_table("entitlement", metadata)
refresh_tokens = template_table.refresh_token_table("refresh_token", metadata)


def database() -> Database:
    """A new SQLite file with auth's tables — a connection per operation, as on a server."""
    path = Path(tempfile.mkdtemp()) / "auth.sqlite3"
    created = Database(f"sqlite:///{path}")
    metadata.create_all(created.engine)
    return created


class TestDatabaseKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        self.clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
        return DatabaseKeyValue(database(), key_values, now=self.clock.now)

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        self.clock.advance(seconds=math.ceil(seconds))


class TestRevocationsOnADatabase(RevocationsContract):
    def make_revocations(self) -> Revocations:
        return KeyValueRevocations(DatabaseKeyValue(database(), key_values))


class TestDatabaseApiKeys(ApiKeyStoreContract):
    def make_store(self) -> ApiKeyStore:
        return DatabaseApiKeys(database(), api_keys)


class TestDatabaseGrants(GrantStoreContract):
    def make_store(self) -> GrantStore:
        return DatabaseGrants(database(), assignments, entitlements)


class TestDatabaseRefreshTokens(RefreshTokensContract):
    def make_store(self) -> RefreshTokens:
        return DatabaseRefreshTokens(database(), refresh_tokens)


def test_the_token_issuer_on_a_database_signs_in_refreshes_and_logs_out() -> None:
    shared = database()
    sessions = TokenIssuer(
        "https://colegio.bo",
        audience="colegio",
        keys=SigningKeys.hmac({"k": "a token secret of thirty-two bytes!"}),
        refresh_tokens=DatabaseRefreshTokens(shared, refresh_tokens),
        revocations=KeyValueRevocations(DatabaseKeyValue(shared, key_values)),
    )
    tokens = sessions.issue(Identity.user("user:MFAQ0123", tenant="cauor"))
    assert tokens.refresh_token is not None
    renewed = sessions.refresh(tokens.refresh_token.get_secret_value())
    assert [one.family for one in sessions.sessions_of("user:MFAQ0123")] == [tokens.session]
    sessions.revoke(renewed.access_token.get_secret_value())
    caller = Credentials(
        transport="http",
        headers={"authorization": f"Bearer {renewed.access_token.get_secret_value()}"},
    )
    with pytest.raises(Unauthenticated, match="session revoked"):
        sessions.provider().authenticate(caller)
