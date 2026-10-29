# Amazon Web Services

Every AWS service the framework speaks is an adapter of a port it already has — the cache store, a
queue, the queue wire's broker, a settings source — so nothing else changes when a service moves to
AWS: the same buses, the same context, the same exposure. All of it is the `[aws]` extra
(`pip install sincpro-framework[aws]`, boto3).

**The simple way needs nothing.** boto3 finds the region and the credentials the way every AWS tool
does — the environment, a profile, the role of the task or the function. A client of the project's
— another region, LocalStack, retries of its own — is handed in as `client=`.

| Need | Service | Adapter | Port |
|---|---|---|---|
| contexts, drafts and pipeline runs shared by every replica; one cron tick across pods; idempotency; query caching | DynamoDB | `DynamoKeyValue` | `KeyValueStore` |
| an event sent out of the process | SNS, SQS, EventBridge | `SnsQueue`, `SqsQueue`, `EventBridgeQueue` | `Queue` |
| a queue consumed by the buses | SQS | `SqsBroker` | `Broker` of the queue wire |
| a function fed by a queue | Lambda + SQS | `sqs_handler` | an entrypoint |
| settings from a parameter store or a vault | SSM Parameter Store, Secrets Manager | `ParameterStore`, `SecretsManager` | `SettingSource` |
| traces, metrics | X-Ray, CloudWatch | — | OTLP to the AWS Distro for OpenTelemetry collector: nothing to write |

## DynamoDB: one store for everything shared

```python
from sincpro_framework.context import KeyValueContexts
from sincpro_framework.data_layer.store import DynamoKeyValue

billing.store_context_in(KeyValueContexts(DynamoKeyValue("billing-shared")))      # simple
DynamoKeyValue("billing-shared", client=dynamodb, prefix="billing:")            # advanced
```

The table is the infrastructure's: one string partition key `key`, and DynamoDB's expiry on
`expires_at`. DynamoDB deletes an expired item when it gets to it, so every read asks the clock: an
item is gone for the store the instant its time passes. It passes the same
`KeyValueStoreContract` as Redis and Memcached — racing claims included — so whatever stands on the
port runs on it: `KeyValueContexts` (contexts, drafts, pipeline runs), `KeyValueRuns` (one cron tick
across pods), `KeyValueRecords` (idempotency, the queue inbox), `QueryCaching`.

## Sending events: SNS, SQS, EventBridge

```python
from sincpro_framework.event_driven import Publisher
from sincpro_framework.event_driven.adapters.aws import EventBridgeQueue, SnsQueue, SqsQueue

Publisher(SnsQueue(topic_arn)).publish(InvoicePaid(...))                         # simple
billing.publish_failures(to=Publisher(SnsQueue(topic_arn)), use_cases=[CommandPay])
billing.publish_completions(to=Publisher(EventBridgeQueue("billing-bus", source="billing")))
SnsQueue(topic_arn, source="urn:svc:billing", attributes_of=my_attributes)       # advanced
```

- **The body is the event**; its envelope — id, wire name, chain, trace, context — travels beside it,
  so the consumer reads it as from any broker.
- **SNS and SQS filter without reading the body**: each message carries `sincpro-event` and, for an
  execution event, `use_case`, `bus`, `level`, `kind`, `tenant_id`, so a subscription filter policy
  is `{"use_case": ["billing.CommandPay"], "kind": ["unavailable"]}`. `attributes_of=` says others
  (ten at most, the envelope one of them).
- **FIFO** topics and queues (`.fifo`) keep one entity's events in order — else one flow's — and
  deliver a retried event once.
- **EventBridge** rules read the body: the detail is `{"metadata": <envelope>, "data": <event>}` and
  the detail type the event's wire name — `{"detail-type": ["billing.invoice.v1.paid"]}`.

What the service does not take is a `ServiceUnavailableError`: retry later — or let
`Publisher(SnsQueue(...), forwarding=True)` hold the event and send it once AWS takes it again
([events](../events/README.md#forwarding-held-and-sent-in-order)).

## Consuming a queue: `SqsBroker`

```python
from sincpro_framework.entrypoints import QueueGateway, subscribe
from sincpro_framework.entrypoints.adapters.queue import SqsBroker

broker = SqsBroker()                                                   # simple
QueueGateway(broker, [billing, accounting]).build()                    # Commands declared, every event heard
await broker.start()                                                   # long polling, until stop()

SqsBroker(client, wait=timedelta(seconds=20), batch=10,                # advanced
          queues={"billing.invoices.issue": "https://sqs…/billing-issue"})
```

It is the queue wire of every broker — producers checked, the inbox, retries, dead letters, the
identity and the context — on SQS. A channel is read from the queue it names, with the characters
SQS refuses turned to `-` (`billing.invoice.v1.issued` → `billing-invoice-v1-issued`), or from the
queue `queues=` says; `broker.queues_read` lists what the infrastructure has to create. A retry makes
the message visible again; a dead letter is the queue's redrive policy — give it a
`maxReceiveCount` equal to the wire's `max_attempts`. A message an SNS subscription or an
EventBridge rule put on the queue is opened, raw delivery or not.

## A Lambda fed by SQS

```python
from sincpro_framework.entrypoints import sqs_handler

handler = sqs_handler([billing])                                       # simple
handler = sqs_handler([billing], options=QueueOptions(max_attempts=5), broker=SqsBroker(queues=...))
```

Every record runs through the same queue wire; the records it did not ack are answered in
`batchItemFailures` — turn on `ReportBatchItemFailures` in the event source mapping — so only they
come back. A function behind API Gateway serves the REST or FastAPI gateway's ASGI app through an
ASGI adapter (Mangum, the Lambda Web Adapter): nothing of the framework's is needed there.

## Settings from SSM and Secrets Manager

```yaml
billing:
  token: $SSM:/billing/prod/token
  database: $SECRET:billing/prod/db#password
```

```python
from sincpro_framework.settings.adapters.aws import ParameterStore, SecretsManager

build_config_obj(Billing, "conf/billing.yml", "billing",
                 sources={"SSM": ParameterStore(), "SECRET": SecretsManager()})
ParameterStore(prefix="/billing/prod/")                                # the file says $SSM:token
```

What cannot be read never stops the process: a source that was not handed in, or one that fails —
no boto3, no permission, no network — leaves the value as written, with a warning; a name the
service does not hold falls back to the default. See [settings](../core/settings.md).

## Where each piece lives, and what holds it

| Adapter | Module | Test |
|---|---|---|
| `DynamoKeyValue` | `sincpro_framework.data_layer.store` | `tests/data_layer/caching/test_provider_dynamodb.py` |
| `SnsQueue` · `SqsQueue` · `EventBridgeQueue` | `sincpro_framework.event_driven.adapters.aws` | `tests/entrypoints/adapters/queue/test_sqs_broker.py` |
| `SqsBroker` | `sincpro_framework.entrypoints.adapters.queue` | `tests/entrypoints/adapters/queue/test_sqs_broker.py` |
| `sqs_handler` | `sincpro_framework.entrypoints` | `tests/entrypoints/adapters/aws_lambda/test_sqs_handler.py` |
| `ParameterStore` · `SecretsManager` | `sincpro_framework.settings.adapters.aws` | `tests/settings/test_aws_sources.py` |

Every test runs on [moto](https://github.com/getmoto/moto): no account, no network.
