"""AWS Lambda as an entrypoint, behind the `[aws]` extra: a batch fed by SQS read as the queue
wire reads a polled queue (`sqs.batch_handler`) — what `sqs_handler` serves."""
