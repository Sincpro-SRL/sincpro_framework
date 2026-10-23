"""`DynamoKeyValue`: the store on an Amazon DynamoDB table — the six operations, nothing more.

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
clearing an expired counter; `take` is a delete that answers what it deleted. Reads are strongly
consistent: a claim written by one replica is what the next one reads. One item holds up to
400 KB.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sincpro_framework.common.aws import aws_client
from sincpro_framework.common.store import KeyValueStore

KEY = "key"
VALUE = "value"
COUNT = "count"
EXPIRES_AT = "expires_at"
BATCH = 100
"""What `BatchGetItem` takes in one call."""


def _conditional_failure(error: Exception) -> bool:
    response = getattr(error, "response", None) or {}
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class DynamoKeyValue(KeyValueStore):
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
        item: dict[str, Any] = {**self._key(key), VALUE: {"B": value}}
        if ttl is not None:
            item[EXPIRES_AT] = {"N": str(self.clock() + max(ttl.total_seconds(), 0.001))}
        return item

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """`BatchGetItem` by hundreds, strongly consistent, again for what DynamoDB left
        unprocessed."""
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
        with self._reaching():
            self.client.put_item(TableName=self.table, Item=self._item(key, value, ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Written when the key is absent or its time passed — one conditional `PutItem`."""
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

    def increment(self, key: str) -> int:
        """`ADD` on a live counter; an expired one is deleted first — by whoever gets there
        first, the others find it gone — and counted again from zero."""
        with self._reaching():
            while True:
                try:
                    answer = self.client.update_item(
                        TableName=self.table,
                        Key=self._key(key),
                        UpdateExpression="ADD #n :one",
                        ConditionExpression="attribute_not_exists(#e) OR #e > :now",
                        ExpressionAttributeNames={"#n": COUNT, "#e": EXPIRES_AT},
                        ExpressionAttributeValues={
                            ":one": {"N": "1"},
                            ":now": {"N": str(self.clock())},
                        },
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
        with self._reaching():
            self.client.delete_item(TableName=self.table, Key=self._key(key))

    def take(self, key: str) -> bytes | None:
        """A `DeleteItem` that answers what it deleted — of many racing, one deletes it."""
        with self._reaching():
            answer = self.client.delete_item(
                TableName=self.table, Key=self._key(key), ReturnValues="ALL_OLD"
            )
            return self._live(answer.get("Attributes"))
