"""`DynamoKeyValue`: the store on an Amazon DynamoDB table — the operations of the port, nothing more.

    DynamoKeyValue("billing-cache")                                  # boto3's default client
    DynamoKeyValue("billing-cache", client=dynamodb, prefix="billing:")

The table is the project's — made by its infrastructure code — with one string partition key,
`key`, and DynamoDB's own expiry turned on for `expires_at`:

    aws dynamodb create-table --table-name billing-cache --billing-mode PAY_PER_REQUEST \
        --attribute-definitions AttributeName=key,AttributeType=S \
        --key-schema AttributeName=key,KeyType=HASH
    aws dynamodb update-time-to-live --table-name billing-cache \
        --time-to-live-specification Enabled=true,AttributeName=expires_at

Context: DynamoDB deletes an expired item when it gets to it — up to days later — so every read
asks the clock, and an expired item is gone for this store the instant its time passes. `add` is
a conditional write that also takes the place of an expired item; `increment` is `ADD`, after
clearing an expired counter; `take` is a delete that answers what it deleted; `replace` a put or a
delete on the condition that the live item holds what was expected. Reads are strongly
consistent: a claim written by one replica is what the next one reads. One item holds up to
400 KB.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sincpro_framework.common.aws import aws_client
from sincpro_framework.data_layer.store.infrastructure.client import ClientKeyValue

KEY = "key"
VALUE = "value"
COUNT = "count"
EXPIRES_AT = "expires_at"
BATCH = 100
"""What `BatchGetItem` takes in one call."""


def _conditional_failure(error: Exception) -> bool:
    """Whether `error` is DynamoDB refusing a write's condition — botocore's `ClientError`
    read by its code, so this module never imports botocore.

    Context: a refused condition is an answer (`add` lost, `replace` saw another value), not a
    failure; every other error is raised.
    """
    response = getattr(error, "response", None) or {}
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class DynamoKeyValue(ClientKeyValue):
    """The `KeyValueStore` port on one DynamoDB table, expiry checked against `clock` on every
    read."""

    def __init__(
        self,
        table: str,
        client: Any = None,
        prefix: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`table` is the table's name; `client` a boto3 DynamoDB client — boto3's default when
        none is given; `clock` the seconds since the epoch, which a test moves by hand."""
        self.table = table
        self.client = aws_client("dynamodb", client)
        self.prefix = prefix
        self.clock = clock

    def _key(self, key: str) -> dict[str, Any]:
        """The item's partition key, prefixed.

            _key("a")  ->  {"key": {"S": "billing:a"}}

        The prefix sets the services that share one table apart.
        """
        return {KEY: {"S": self.prefix + key}}

    def _live(self, item: dict[str, Any] | None) -> bytes | None:
        """What an item holds, or `None` when there is none or its time passed."""
        if not item:
            return None
        expires = item.get(EXPIRES_AT)
        if expires is not None and float(expires["N"]) <= self.clock():
            return None
        if VALUE in item:
            return bytes(item[VALUE]["B"])
        if COUNT in item:
            return item[COUNT]["N"].encode()
        return None

    def _item(self, key: str, value: bytes, ttl: timedelta | None) -> dict[str, Any]:
        """The item a put writes: the key, the bytes, and `expires_at` in epoch seconds when
        there is a ttl — never zero or less, so a zero ttl still expires rather than lives."""
        item: dict[str, Any] = {**self._key(key), VALUE: {"B": value}}
        if ttl is not None:
            item[EXPIRES_AT] = {"N": str(self.clock() + max(ttl.total_seconds(), 0.001))}
        return item

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """`BatchGetItem` by hundreds, strongly consistent, again for what DynamoDB left
        unprocessed.

            store.get_many(["open", "tripped", "open"])   ->  [b"1729…", None, b"1729…"]

        1. The keys prefixed and each asked once, in the order first named.
        2. By batches of 100 (`BATCH`), `ConsistentRead`:
           2.1 each item found is read as live or not (`_live`) — expired, `None`;
           2.2 what DynamoDB left in `UnprocessedKeys` is asked again, until nothing is.
        Final: one answer per key asked, in its order — `None` for a key with no item.
        """
        with self._reaching():
            found: dict[str, bytes | None] = {}
            unique = list(dict.fromkeys(self.prefix + key for key in keys))
            for start in range(0, len(unique), BATCH):
                asked: dict[str, Any] = {
                    self.table: {
                        "Keys": [{KEY: {"S": one}} for one in unique[start : start + BATCH]],
                        "ConsistentRead": True,
                    }
                }
                while asked:
                    answer = self.client.batch_get_item(RequestItems=asked)
                    for item in answer.get("Responses", {}).get(self.table, []):
                        found[item[KEY]["S"]] = self._live(item)
                    asked = answer.get("UnprocessedKeys") or {}
            return [found.get(self.prefix + key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """An unconditional `PutItem` — it replaces whatever the item held, a counter too."""
        with self._reaching():
            self.client.put_item(TableName=self.table, Item=self._item(key, value, ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Written when the key is absent or its time passed — one conditional `PutItem`.

            store.add("probe", b"1", timedelta(seconds=30))   ->  True    # nobody held it
            store.add("probe", b"1", timedelta(seconds=30))   ->  False   # held, still live

        A refused condition is the answer `False`; any other error of DynamoDB is raised."""
        with self._reaching():
            try:
                self.client.put_item(
                    TableName=self.table,
                    Item=self._item(key, value, ttl),
                    ConditionExpression="attribute_not_exists(#k) OR #e <= :now",
                    ExpressionAttributeNames={"#k": KEY, "#e": EXPIRES_AT},
                    ExpressionAttributeValues={":now": {"N": str(self.clock())}},
                )
            except Exception as error:
                if _conditional_failure(error):
                    return False
                raise
            return True

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        """`ADD` on a live counter; an expired one is deleted first — by whoever gets there
        first, the others find it gone — and counted again from zero.

        1. `UpdateItem ADD count 1`, on the condition that the item has no expiry or it is
           still ahead — a missing item is created at 1. With `ttl`, the same update sets
           `expires_at`: the count and its expiry in one write.
        2. The condition refused: the item expired. Deleted on the condition that it still is,
           a refusal there meaning another caller already cleared or renewed it; back to 1.
        Final: the counter after this caller's addition.

            store.increment("failures")   ->  1        # no item: created at 1
            store.increment("failures")   ->  2
            store.increment("failures")   ->  1        # its expires_at had passed

        It loops until an `ADD` passes; only an error that is not a refused condition leaves.
        """
        with self._reaching():
            while True:
                try:
                    now = self.clock()
                    values = {":one": {"N": "1"}, ":now": {"N": str(now)}}
                    expression = "ADD #n :one"
                    if ttl is not None:
                        values[":until"] = {"N": str(now + max(ttl.total_seconds(), 0.001))}
                        expression = "ADD #n :one SET #e = :until"
                    answer = self.client.update_item(
                        TableName=self.table,
                        Key=self._key(key),
                        UpdateExpression=expression,
                        ConditionExpression="attribute_not_exists(#e) OR #e > :now",
                        ExpressionAttributeNames={"#n": COUNT, "#e": EXPIRES_AT},
                        ExpressionAttributeValues=values,
                        ReturnValues="UPDATED_NEW",
                    )
                    return int(answer["Attributes"][COUNT]["N"])
                except Exception as error:
                    if not _conditional_failure(error):
                        raise
                try:
                    self.client.delete_item(
                        TableName=self.table,
                        Key=self._key(key),
                        ConditionExpression="#e <= :now",
                        ExpressionAttributeNames={"#e": EXPIRES_AT},
                        ExpressionAttributeValues={":now": {"N": str(self.clock())}},
                    )
                except Exception as error:
                    if not _conditional_failure(error):
                        raise

    def delete(self, key: str) -> None:
        """An unconditional `DeleteItem` — nothing happens when the item is not there."""
        with self._reaching():
            self.client.delete_item(TableName=self.table, Key=self._key(key))

    def take(self, key: str) -> bytes | None:
        """A `DeleteItem` that answers what it deleted — of many racing, one deletes it."""
        with self._reaching():
            answer = self.client.delete_item(
                TableName=self.table, Key=self._key(key), ReturnValues="ALL_OLD"
            )
            return self._live(answer.get("Attributes"))

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """A `PutItem` — a `DeleteItem` for `None` — on the condition that the item is live and
        holds `expected`: one conditional write, so of many racing exactly one passes.

            store.replace("lease", b"owner-a", b"owner-b", timedelta(seconds=30))  ->  True
            store.replace("lease", b"owner-a", None)   ->  False   # it holds owner-b now
            store.replace("gone", b"x", b"y")          ->  False   # no item, or expired

        A refused condition is the answer `False`; any other error of DynamoDB is raised."""
        condition = {
            "ConditionExpression": "#v = :expected AND (attribute_not_exists(#e) OR #e > :now)",
            "ExpressionAttributeNames": {"#v": VALUE, "#e": EXPIRES_AT},
            "ExpressionAttributeValues": {
                ":expected": {"B": expected},
                ":now": {"N": str(self.clock())},
            },
        }
        with self._reaching():
            try:
                if value is None:
                    self.client.delete_item(
                        TableName=self.table, Key=self._key(key), **condition
                    )
                else:
                    self.client.put_item(
                        TableName=self.table, Item=self._item(key, value, ttl), **condition
                    )
            except Exception as error:
                if _conditional_failure(error):
                    return False
                raise
            return True
