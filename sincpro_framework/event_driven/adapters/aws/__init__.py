"""Events to Amazon SNS, SQS and EventBridge — the sending side, behind the `[aws]` extra.

    from sincpro_framework.event_driven.adapters.aws import SnsQueue, SqsQueue, EventBridgeQueue

    Publisher(SnsQueue(topic_arn)).publish(InvoicePaid(...))         # a Queue like any other

The listening side is an entrypoint — what outsiders make this process do: the queue wire on an
`SqsBroker` (`sincpro_framework.entrypoints.adapters.queue`), or a Lambda fed by SQS
(`sincpro_framework.entrypoints.sqs_handler`).
"""

from sincpro_framework.event_driven.adapters.aws.attributes import filtered_on
from sincpro_framework.event_driven.adapters.aws.queues import (
    EventBridgeQueue,
    SnsQueue,
    SqsQueue,
)

__all__ = ["EventBridgeQueue", "SnsQueue", "SqsQueue", "filtered_on"]
