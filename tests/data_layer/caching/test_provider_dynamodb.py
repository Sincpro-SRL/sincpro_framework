"""The DynamoDB store honours the same `KeyValueStore` contract as the one in memory — on moto's
DynamoDB, with a clock the test moves, so no account and no waiting is needed."""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from sincpro_framework.runtime.testing import KeyValueStoreContract  # noqa: E402
from sincpro_framework.store import KeyValueStore  # noqa: E402
from sincpro_framework.store.adapters.dynamodb import (  # noqa: E402
    DynamoKeyValue,
)

TABLE = "sincpro-cache"


@pytest.fixture(autouse=True)
def aws(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        yield


class ManualClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class OneRequestAtATime:
    """A boto3 client whose requests reach moto one at a time. DynamoDB applies each request
    atomically — an `ADD` on a missing item, a conditional `PutItem` — while moto's backend reads
    and writes its dicts with no lock: racing threads lose counts, or break its iteration. The
    racing contract tests prove the store's own logic, so they need what DynamoDB guarantees.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self.lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        found = getattr(self.client, name)
        if not callable(found):
            return found

        def serialized(*args: Any, **kwargs: Any) -> Any:
            with self.lock:
                return found(*args, **kwargs)

        return serialized


def a_table() -> None:
    client = boto3.client("dynamodb")
    if TABLE in client.list_tables()["TableNames"]:
        client.delete_table(TableName=TABLE)
    client.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
    )


class TestDynamoKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        a_table()
        return DynamoKeyValue(
            TABLE,
            client=OneRequestAtATime(boto3.client("dynamodb")),
            prefix="test:",
            clock=ManualClock(),
        )

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        assert isinstance(store, DynamoKeyValue) and isinstance(store.clock, ManualClock)
        store.clock.now += seconds


def test_more_keys_than_one_batch_are_read_in_order():
    a_table()
    store = DynamoKeyValue(TABLE)
    for number in range(150):
        store.set(f"k{number}", str(number).encode())

    found = store.get_many([f"k{number}" for number in range(149, -1, -1)] + ["k3", "none"])

    assert found[:2] == [b"149", b"148"] and found[-2:] == [b"3", None]


def test_an_expired_counter_starts_again():
    a_table()
    clock = ManualClock()
    store = DynamoKeyValue(TABLE, clock=clock)
    store.set("counter", b"9", ttl=None)
    store.delete("counter")
    assert store.increment("counter") == 1
    store.client.update_item(
        TableName=TABLE,
        Key={"key": {"S": "counter"}},
        UpdateExpression="SET expires_at = :then",
        ExpressionAttributeValues={":then": {"N": str(clock.now + 1)}},
    )
    clock.now += 2

    assert store.increment("counter") == 1
    assert store.get("counter") == b"1"


def test_the_whole_context_store_runs_on_it():
    """Drafts, pipeline runs and shared contexts are entries of a `KeyValueContexts`."""
    from sincpro_framework.context import KeyValueContexts, use_context

    a_table()
    contexts = KeyValueContexts(DynamoKeyValue(TABLE))
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}) as context:
        drafts = context.drafts(store=contexts)
        kept = drafts.save("billing.Invoice:7", {"discount": "10"}, 3, 0)
        assert drafts.read("billing.Invoice:7") == kept
        assert drafts.subjects() == ["billing.Invoice:7"]
