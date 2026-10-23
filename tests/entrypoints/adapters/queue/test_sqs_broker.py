"""The queue wire on Amazon SQS (`SqsBroker`), on moto's SQS, SNS and EventBridge: the same
path every broker's delivery takes — a Command consumed from its queue, retried and left to the
redrive policy, a producer refused before the bus, an event fanned out by SNS or routed by
EventBridge to the buses that hear it."""

import asyncio
import json
from collections.abc import Iterator
from datetime import timedelta

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from sincpro_framework.entrypoints.adapters.queue import (  # noqa: E402
    QueueGateway,
    SqsBroker,
    cloud_event_headers,
    queue_name,
)
from sincpro_framework.event_driven import Publisher  # noqa: E402
from sincpro_framework.event_driven.adapters.aws import (  # noqa: E402
    HEADERS_ATTRIBUTE,
    EventBridgeQueue,
    SnsQueue,
    SqsQueue,
)

from .test_queue_gateway import (  # noqa: E402
    ISSUE_CHANNEL,
    SALES,
    CommandIssueInvoice,
    InvoiceIssued,
    accounting_bus,
    billing_bus,
)


@pytest.fixture(autouse=True)
def aws(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        yield


def a_queue(name: str) -> str:
    return boto3.client("sqs").create_queue(QueueName=name)["QueueUrl"]


def polled(broker: SqsBroker, times: int = 1) -> None:
    for queue in broker.queues_read:
        if not queue.startswith("https://"):
            a_queue(queue)

    async def scenario() -> None:
        for _ in range(times):
            await broker.poll_once()

    asyncio.run(scenario())


def issue(amount: int, source: str = SALES) -> None:
    command = CommandIssueInvoice(customer_id=7, amount=amount)
    headers = cloud_event_headers(command, source=source, id=f"msg-{amount}")
    boto3.client("sqs").send_message(
        QueueUrl=a_queue(queue_name(ISSUE_CHANNEL)),
        MessageBody=command.model_dump_json(),
        MessageAttributes={
            HEADERS_ATTRIBUTE: {"DataType": "String", "StringValue": json.dumps(headers)}
        },
    )


def waiting(queue: str) -> int:
    attributes = boto3.client("sqs").get_queue_attributes(
        QueueUrl=a_queue(queue), AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])


def gateway_on_sqs(ran: list[str]) -> SqsBroker:
    broker = SqsBroker(wait=timedelta(0))
    QueueGateway(broker, [billing_bus(ran), accounting_bus(ran)], unguarded=True).build()
    return broker


def test_a_command_is_consumed_from_its_queue_and_deleted():
    ran: list[str] = []
    broker = gateway_on_sqs(ran)
    issue(100)

    polled(broker)

    assert ran == ["issued 7:100"]
    assert waiting(queue_name(ISSUE_CHANNEL)) == 0


def test_a_failing_command_is_visible_again_and_counted_until_the_redrive():
    ran: list[str] = []
    broker = gateway_on_sqs(ran)
    issue(0)  # the database went away

    polled(broker, times=2)

    assert ran == []
    message = boto3.client("sqs").receive_message(
        QueueUrl=a_queue(queue_name(ISSUE_CHANNEL)),
        MessageSystemAttributeNames=["ApproximateReceiveCount"],
    )["Messages"][0]
    assert int(message["Attributes"]["ApproximateReceiveCount"]) == 3


def test_a_producer_not_allowed_is_refused_before_the_bus():
    ran: list[str] = []
    broker = gateway_on_sqs(ran)
    issue(100, source="urn:svc:intruder")

    polled(broker)

    assert ran == []


def test_an_event_published_to_sns_is_fanned_out_and_heard():
    """The whole road: a bus publishes to a topic, SNS delivers to the queue of every
    subscriber, the queue wire on SQS hands it to the bus that hears it."""
    heard: list[str] = []
    sns = boto3.client("sns")
    topic = sns.create_topic(Name="billing-events")["TopicArn"]
    queue_url = a_queue(queue_name(InvoiceIssued.name))
    queue_arn = boto3.client("sqs").get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)
    broker = SqsBroker(wait=timedelta(0))
    QueueGateway(broker, [accounting_bus(heard)], unguarded=True).build()

    Publisher(SnsQueue(topic, source="urn:svc:billing")).publish(InvoiceIssued(number="F-1"))
    polled(broker)

    assert heard == ["booked F-1"]


def test_an_sns_subscription_filters_on_what_the_queue_says_beside_the_body():
    sns = boto3.client("sns")
    topic = sns.create_topic(Name="executions")["TopicArn"]
    queue_url = a_queue("failures-of-pay")
    queue_arn = boto3.client("sqs").get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    sns.subscribe(
        TopicArn=topic,
        Protocol="sqs",
        Endpoint=queue_arn,
        Attributes={"FilterPolicy": json.dumps({"sincpro-event": [InvoiceIssued.name]})},
    )
    queue = SnsQueue(topic)

    queue.put(InvoiceIssued(number="F-2"))
    from .test_queue_gateway import InvoiceVoided

    queue.put(InvoiceVoided(number="F-3"))

    assert waiting("failures-of-pay") == 1


def test_an_event_routed_by_eventbridge_is_heard():
    heard: list[str] = []
    events = boto3.client("events")
    queue_url = a_queue(queue_name(InvoiceIssued.name))
    queue_arn = boto3.client("sqs").get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    events.put_rule(
        Name="issued", EventPattern=json.dumps({"detail-type": [InvoiceIssued.name]})
    )
    events.put_targets(Rule="issued", Targets=[{"Id": "accounting", "Arn": queue_arn}])
    broker = SqsBroker(wait=timedelta(0))
    QueueGateway(broker, [accounting_bus(heard)], unguarded=True).build()

    Publisher(EventBridgeQueue(source="billing")).publish(InvoiceIssued(number="F-4"))
    polled(broker)

    assert heard == ["booked F-4"]


def test_an_event_sent_to_sqs_is_heard_once_by_its_own_id():
    heard: list[str] = []
    queue_url = a_queue(queue_name(InvoiceIssued.name))
    broker = SqsBroker(wait=timedelta(0))
    QueueGateway(broker, [accounting_bus(heard)], unguarded=True).build()
    event = InvoiceIssued(number="F-5")

    SqsQueue(queue_url).put(event)
    SqsQueue(queue_url).put(event)  # the same event, sent twice
    polled(broker, times=2)

    assert heard == ["booked F-5"]


def test_a_channel_may_be_read_from_the_queue_the_project_names():
    ran: list[str] = []
    url = a_queue("billing-issue-prod")
    broker = SqsBroker(wait=timedelta(0), queues={ISSUE_CHANNEL: url})
    QueueGateway(broker, [billing_bus(ran)], unguarded=True).build()
    command = CommandIssueInvoice(customer_id=7, amount=9)
    boto3.client("sqs").send_message(
        QueueUrl=url,
        MessageBody=command.model_dump_json(),
        MessageAttributes={
            HEADERS_ATTRIBUTE: {
                "DataType": "String",
                "StringValue": json.dumps(cloud_event_headers(command, source=SALES)),
            }
        },
    )

    polled(broker)

    assert ran == ["issued 7:9"]
