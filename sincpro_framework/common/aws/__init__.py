"""Amazon Web Services, behind the `[aws]` extra: the one way every AWS adapter gets its client.

    DynamoKeyValue("billing-cache")                                  # boto3's default client
    DynamoKeyValue("billing-cache", client=boto3.client("dynamodb", region_name="us-east-1"))

Context: the simple way needs nothing — boto3 finds the region and the credentials the way every
AWS tool does (environment, profile, the role of the task or the function). A client of the
project's — another region, an endpoint such as LocalStack, retries of its own — is handed in.

| Port | Adapter | Where |
|---|---|---|
| `KeyValueStore` | `DynamoKeyValue` | `sincpro_framework.data_layer.caching.adapters.dynamodb` |
| `Queue` | `SnsQueue`, `SqsQueue`, `EventBridgeQueue` | `sincpro_framework.event_driven.adapters.aws` |
| `Broker` (the queue wire) | `SqsBroker` | `sincpro_framework.event_driven.adapters.aws` |
| an entrypoint | `sqs_handler` (a Lambda fed by SQS) | `sincpro_framework.entrypoints.adapters.aws_lambda` |
| `SettingSource` | `ParameterStore`, `SecretsManager` | `sincpro_framework.settings.adapters.aws` |
"""

from sincpro_framework.common.aws.clients import AWS_MISSING, aws_client

__all__ = ["AWS_MISSING", "aws_client"]
