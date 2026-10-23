"""AWS Lambda as an entrypoint, behind the `[aws]` extra: a function fed by SQS runs every
message through the queue wire (`sqs_handler`).

    handler = sqs_handler([billing])

A function behind API Gateway serves the FastAPI or REST gateway's ASGI app through an ASGI
adapter of the project's choice (Mangum, AWS Lambda Web Adapter): nothing of the framework's is
needed there.
"""

from sincpro_framework.entrypoints.adapters.aws_lambda.sqs import sqs_handler

__all__ = ["sqs_handler"]
