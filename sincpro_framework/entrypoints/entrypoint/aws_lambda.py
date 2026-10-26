"""`sqs_handler`: an AWS Lambda fed by SQS that runs every message through the queue wire — the
same path, the same exposure, the same settling as a process polling the queue — behind the
`[aws]` extra.

    # billing/lambda_function.py
    from sincpro_framework.entrypoints import sqs_handler

    handler = sqs_handler([billing])           # what the function's configuration names

Context: Lambda receives a batch and deletes it when the function returns. Turn on
`ReportBatchItemFailures` in the event source mapping: a message the wire did not ack is answered
in `batchItemFailures`, so only it comes back (`adapters.aws_lambda.sqs`). A function behind API
Gateway serves the FastAPI or REST gateway's ASGI app through an ASGI adapter of the project's
choice (Mangum, AWS Lambda Web Adapter): nothing of the framework's is needed there.
"""

from collections.abc import Callable, Sequence
from typing import Any

from sincpro_framework.entrypoints.adapters.aws_lambda.sqs import batch_handler
from sincpro_framework.entrypoints.adapters.queue.sqs import SqsBroker
from sincpro_framework.entrypoints.adapters.queue.wire import QueueOptions
from sincpro_framework.entrypoints.entrypoint.queue import QueueGateway
from sincpro_framework.use_bus import UseFramework


def sqs_handler(
    buses: Sequence[UseFramework],
    options: QueueOptions | None = None,
    broker: SqsBroker | None = None,
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """The function Lambda calls: every record run through the queue wire of a `QueueGateway`
    over `buses`, the ones it did not ack answered as failures.

        in      {"Records": [three messages, the second fails]}
        out     {"batchItemFailures": [{"itemIdentifier": "<id of the second>"}]}

    `options` is the wire's (`QueueOptions`: the inbox, `max_attempts`, ...); `broker` an
    `SqsBroker` of the project's, for the client a retry by channel publishes with.

        handler = sqs_handler([billing])
        handler({"Records": [<from billing-invoice-v1-issued, acked>]}, None)
        ->  {"batchItemFailures": []}
        handler({"Records": [<from a queue nobody reads, id "m-9">]}, None)
        ->  {"batchItemFailures": [{"itemIdentifier": "m-9"}]}

    1. The broker given, else a new `SqsBroker`.
    2. A `QueueGateway` over the broker and `buses`, with the wire's `options`.
    Final: its subscriptions, built once, served by the Lambda batch handler
    (`batch_handler`).
    """
    sqs = broker or SqsBroker()
    gateway = QueueGateway(sqs, list(buses), options=options)
    return batch_handler(sqs, gateway.queue_wire, gateway.build())
