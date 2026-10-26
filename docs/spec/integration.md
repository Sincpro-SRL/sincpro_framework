# Integration specification

How a Sincpro service talks to the world beyond its buses: what an execution announces and who
hears it, how events leave the process and how messages come in through any broker, the cloud
services it speaks, where settings come from, and the jobs of several steps it runs. Updated in
place with every change (see [how a specification is kept](README.md)).

**Status:** ✅ built and held by a test · 🟡 partly · ⬜ not built.

## 0. Simple first, the rest when needed

Every capability below is written in steps, and a project writes only the step it needs. The first
step is one line that works with every default; each later one is an option of the same call, never
another API to learn; the last is a port of the project's own.

| Capability | 1 — the line that works | 2 — an option | 3 — more options | 4 — the project's own |
|---|---|---|---|---|
| Hearing an execution | `bus.on_failure(alert)` | `use_cases=[...]` | `when=Condition(...)` | a function that decides |
| Sending events | `Publisher(SnsQueue(arn))` | `source=`, `use_cases=` on `publish_*` | `attributes_of=`, FIFO | a `Queue`: `put` / `aput` |
| Consuming a queue | `QueueGateway(SqsBroker(), [bus])` | `QueueOptions(max_attempts=…)` | `SqsBroker(queues=…, wait=…)` | a `Broker` with its `flavour` |
| Settings | `$ENV:NAME` | `sources={"SSM": ParameterStore()}` | `ParameterStore(prefix=…, client=…)` | a function `name → value` |
| A job of steps | `steps = [...]` | `retry = Retry()` | `Await("approval")`, `by`, `resume` | `answer()`, `records = False` |
| A shared store | `InMemoryContexts()` | `KeyValueContexts(RedisKeyValue(...))` | `DynamoKeyValue(table, client=…)` | a `KeyValueStore` with its contract |

**Why.** An API that asks every option before the first call is learned by nobody: the defaults are
what most services want, and an option is reached the day it is needed — from the same call, so
nothing written before is rewritten. What is safe to ignore is the default; what could surprise is
said in the logs, never refused, unless it cannot work at all: the framework facilitates, it does
not enforce.

## 1. What an execution announces, and who hears it

Every use case announces that it began (`ExecutionStarted`) and how it ended (`ExecutionCompleted`,
`ExecutionFailed`), with the same `execution_id`. Pages: [incomes](../core/incomes.md),
[outcomes](../core/outcomes.md), [the execution path](../core/execution-path.md).

| Capability | Status | What it is | Held by |
|---|---|---|---|
| Execution events | ✅ | started, completed, failed — for every Feature and ApplicationService, pipelines and their steps included; nothing is built when nobody hears | `tests/bus_pipeline/incomes/test_incomes.py`, `tests/bus_pipeline/outcomes/test_outcomes.py` |
| Listeners of a bus and of the process | ✅ | `on_start` / `on_completion` / `on_failure`, `start_listeners` / `completion_listeners` / `failure_listeners`; a listener that raises reaches nothing | `tests/bus_pipeline/test_listeners.py` |
| Handed to a queue | ✅ | `publish_starts` / `publish_completions` / `publish_failures(to=Publisher(...))` | `tests/bus_pipeline/outcomes/test_outcomes.py` |
| **Hearing less** | ✅ | `use_cases=` (Commands or their names) and `when=` (a filter of `ddd.criteria` over the event by path: `context.tenant_id`, `dto.amount`, `kind`), on every way to listen | `tests/bus_pipeline/test_listeners.py` |
| Delivered durably | ⬜ | an execution event through the outbox and its relay, for an audit trail that cannot lose one | — |

**Rejected:** a filter language of our own, or SNS's JSON policy. The criteria a query uses is the
one vocabulary; a project that needs more writes a function.

## 2. Events out, messages in: any broker

| Capability | Status | What it is | Held by |
|---|---|---|---|
| The envelope | ✅ | CloudEvents 1.0 in binary mode plus the context and the trace — one place, `Envelope.headers_for`, stamped by every sending queue | `tests/entrypoints/adapters/queue/test_queue_settlement.py` |
| The queue wire on any broker | ✅ | `QueueWire` needs a `Broker` and a `Delivery` (`sincpro_framework.event_driven`); a broker that is not FastStream's declares its `flavour` | `tests/entrypoints/adapters/queue/test_sqs_broker.py` |
| FastStream brokers | ✅ | Kafka, RabbitMQ, Redis, NATS — `[faststream]` | `tests/entrypoints/adapters/queue/test_queue_settlement.py` |
| Amazon SQS | ✅ | `SqsBroker`: competing consumers, receive count, visibility as retry, redrive as dead letter; SNS and EventBridge wrappers opened | `tests/entrypoints/adapters/queue/test_sqs_broker.py` |
| Amazon SNS, SQS, EventBridge (sending) | ✅ | `SnsQueue`, `SqsQueue`, `EventBridgeQueue`; filter attributes, FIFO grouping | `tests/entrypoints/adapters/queue/test_sqs_broker.py` |
| A redelivery runs only the bus that failed | ✅ | `Subscriber(..., receipts=store)`: a receipt per bus and event, kept `keep_for` in a `KeyValueStore`; without one every bus runs again | `tests/event_driven/test_subscriber.py` |
| A Lambda fed by SQS | ✅ | `sqs_handler`: the same wire, `batchItemFailures` | `tests/entrypoints/adapters/aws_lambda/test_sqs_handler.py` |
| A Lambda behind API Gateway | 🟡 | the REST / FastAPI gateway's ASGI app through an adapter of the project's choice; nothing of the framework's | — |
| The queue wire without FastStream installed | ✅ | importing `entrypoints.adapters.queue` needs no broker library | `tests/core/test_core_without_extras.py` |

## 3. Shared stores

| Capability | Status | What it is | Held by |
|---|---|---|---|
| One port for every shared thing | ✅ | `KeyValueStore` (six operations, and `replace` — compare before writing, not abstract) under contexts, drafts, pipeline runs, leases, cron ticks, idempotency, query caching | `sincpro_framework/runtime/testing/contracts/key_value_store.py` |
| **Leases** | ✅ | `Leases`: a name held by one owner at a time, renewed in the background, `FREE` / `HELD` / `EXPIRED`, a fence per take; on any store with `replace` | `tests/common/test_leases.py` |
| Redis / Valkey, Memcached | ✅ | `[redis]`, `[memcached]` | `tests/data_layer/caching/test_provider_key_values.py` |
| DynamoDB | ✅ | `DynamoKeyValue`: conditional writes, `ADD`, delete-and-answer; expiry asked of the clock on every read | `tests/data_layer/caching/test_provider_dynamodb.py` |

## 4. Settings sources

| Capability | Status | What it is | Held by |
|---|---|---|---|
| The environment | ✅ | `$ENV:NAME`, and by path behind `env_prefix` | `tests/settings/test_resolution.py` |
| Any other source | ✅ | `$<SOURCE>:name` read from `sources={...}` — a function `name → value`; the same fallback rules | `tests/settings/test_resolution.py` |
| Never stopping the start | ✅ | a source not handed in, or one that fails, keeps the value as written with a warning | `tests/settings/test_resolution.py` |
| SSM Parameter Store, Secrets Manager | ✅ | `ParameterStore`, `SecretsManager`: a key of a JSON secret with `#key`; the client made at the first read | `tests/settings/test_aws_sources.py` |

## 5. Jobs of several steps: `Pipeline`

Page: [pipelines](../core/pipelines.md).

| Capability | Status | What it is | Held by |
|---|---|---|---|
| A recipe as data | ✅ | `steps`, picked by a field (`by`), `always` last, each step filled by the names of its fields | `tests/pipelines/test_pipelines.py` |
| The run kept in the context | ✅ | per owner (tenant, `owned_by`), one entry per run, a second writer loses with `EntryConflict` | `tests/pipelines/test_pipelines.py`, `tests/context/test_owners.py` |
| Continuing a run | ✅ | the same `run_id` restarts, or continues after the last step that succeeded (`Resume.CONTINUE`) | `tests/pipelines/test_pipelines.py` |
| **Retrying a step** | ✅ | `retry = Retry()`: only what is worth it by its kind, waiting what the error says, growing, bounded; every attempt counted | `tests/pipelines/test_pipeline_retry_await_reads.py` |
| **Waiting for a decision** | ✅ | `Await("approval")`: the run waits; the same run with the field filled goes on, and later steps are given it | `tests/pipelines/test_pipeline_retry_await_reads.py` |
| **A run under a lease** | ✅ | `lease = timedelta(...)`: one run of a `run_id` at a time across replicas, renewed while steps run, a lost lease stops it before its next step; `abandoned(pipeline)` | `tests/pipelines/test_pipeline_leases.py` |
| **Reading the runs** | ✅ | `GetRun[P]`, `FindRuns[P]` answered by `PipelineReads`, on any wire; `RunNotFound` is `not_found` | `tests/pipelines/test_pipeline_retry_await_reads.py` |
| Steps at the same time (`Parallel`, `Map`) | ⬜ | no case asked for it yet | — |
| A wait on the clock | ⬜ | a cron continues the run: no timer of its own | — |

**Rejected:** replaying a function as Lambda durable functions and Temporal do. It asks the code to be
deterministic; here each step is a use case with its own checkpoint — the run in the context — which
the bus already runs, observes and exposes.
