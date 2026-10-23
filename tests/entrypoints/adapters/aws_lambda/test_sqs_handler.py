"""A Lambda fed by SQS runs each record through the queue wire, and answers the ones it did not
ack as `batchItemFailures` — so only they come back."""

import json
from collections.abc import Iterator
from typing import Any

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from sincpro_framework.entrypoints.adapters.aws_lambda import sqs_handler  # noqa: E402
from sincpro_framework.entrypoints.adapters.queue import (  # noqa: E402
    cloud_event_headers,
    queue_name,
)
from sincpro_framework.event_driven.adapters.aws import HEADERS_ATTRIBUTE  # noqa: E402

from ..queue.test_queue_gateway import (  # noqa: E402
    ISSUE_CHANNEL,
    SALES,
    CommandIssueInvoice,
    billing_bus,
)


@pytest.fixture(autouse=True)
def aws(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        yield


def record(amount: int, queue: str = queue_name(ISSUE_CHANNEL)) -> dict[str, Any]:
    """A record as Lambda hands it: lowercase keys, the attributes as `stringValue`."""
    command = CommandIssueInvoice(customer_id=7, amount=amount)
    headers = cloud_event_headers(command, source=SALES, id=f"msg-{amount}")
    return {
        "messageId": f"m-{amount}",
        "receiptHandle": f"r-{amount}",
        "body": command.model_dump_json(),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {
            HEADERS_ATTRIBUTE: {"stringValue": json.dumps(headers), "dataType": "String"}
        },
        "eventSource": "aws:sqs",
        "eventSourceARN": f"arn:aws:sqs:us-east-1:123456789012:{queue}",
    }


def test_the_records_the_wire_acked_are_done_and_the_others_come_back():
    ran: list[str] = []
    handler = sqs_handler([billing_bus(ran)])

    answer = handler({"Records": [record(100), record(0), record(200)]}, None)

    assert ran == ["issued 7:100", "issued 7:200"]
    assert answer == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}


def test_a_record_of_a_queue_nobody_subscribed_comes_back():
    handler = sqs_handler([billing_bus([])])

    answer = handler({"Records": [record(100, queue="somebody-else")]}, None)

    assert answer == {"batchItemFailures": [{"itemIdentifier": "m-100"}]}
