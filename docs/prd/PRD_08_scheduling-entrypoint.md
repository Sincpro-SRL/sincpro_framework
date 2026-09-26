# PRD_08: Scheduling — a clock is one more entrypoint

- **Status**: proposal
- **Depends on**: nothing (PRD_06 for schedules defined as data)
- **Research**: scratchpad `08_webhooks_scheduling_sandbox.md` §B

## Problem

Services need work to happen on a clock — close the cash registers at 02:00, sync SIAT catalogs
every hour, expire quotations daily — and the framework has nothing for it. Each project wires
Celery beat, a Kubernetes CronJob or a loop of its own, and meets the same failures:

- **Duplicate runs**: three replicas, three runs (Celery beat, APScheduler 3 and taskiq all need a
  singleton scheduler).
- **Missed runs**: a deploy or an outage at 02:00 and the job silently never happens.
- **Time**: timezones, DST gaps and repeats, "only on business days".
- **Silent death**: a schedule that stopped firing looks exactly like one with nothing to do.

## Goals

1. Declare **what runs when** next to the use case, in the framework's style: a Command on a bus.
2. Run it with **any provider** — in process, Postgres, Celery, Temporal, DBOS, Kubernetes —
   without the use case knowing which.
3. **One run per tick**, whatever the provider and however many replicas.
4. Explicit **time policies**: timezone, overlap, missed runs, windows, jitter, timeout.
5. Every tick observable: span, lag, outcome, last success.

## Non-goals

- Durable multi-step workflows (wait three days, then escalate) — the provider's own model
  (Temporal, DBOS) or PRD workflows, not this.
- Durable multi-step processes beyond "one Command at one moment" (see *Later* below).

## Prior art

| | Model | What we take |
|---|---|---|
| DBOS `@DBOS.scheduled` | Idempotency key = schedule name + scheduled time; one worker per tick on shared Postgres | The **dedup key** and Postgres as the default |
| Temporal Schedules | Overlap policy (Skip default), catchup window, pause | The **policy vocabulary** |
| Kubernetes CronJob | `concurrencyPolicy`, `timeZone`, `startingDeadlineSeconds` | Mapping for the manifest export |
| Celery beat | Single scheduler process, duplicates if two run | What the default must **not** depend on |
| taskiq | Broker-agnostic tasks and schedule sources | The **provider** abstraction |

## The model

A tick is just another caller, like MCP or gRPC: it builds a DTO and executes it on the bus. The
use case is an ordinary Feature and does not know it is scheduled.

```python
from datetime import datetime

class CommandCloseCashRegisters(DataTransferObject):
    scheduled_for: datetime          # filled by the clock: the tick this run answers


@billing.feature(CommandCloseCashRegisters)
class CloseCashRegisters(Feature):
    def execute(self, dto: CommandCloseCashRegisters) -> None: ...


billing.schedule(
    CommandCloseCashRegisters,
    Cron("0 2 * * *", timezone="America/La_Paz"),
    name="close-cash-registers",
    overlap=Overlap.SKIP,
    missed=Missed.RUN_LATEST,
)
```

A schedule is **registered, not run**: `billing.schedule(...)` records a `Schedule` in the bus
catalog, next to its Features. Nothing fires until an entrypoint runs the clock:

```python
SchedulerGateway({"billing": billing, "siat": siat}, provider=PostgresClock(database)).run()
```

The name follows the other entrypoints — `RpcGateway`, `GrpcGateway` — and so does the shape:
several buses, one process.

## Declaring when

| Trigger | Example |
|---|---|
| `Cron(expression, timezone=)` | `Cron("0 2 * * *", timezone="America/La_Paz")` |
| `Every(...)` | `Every(minutes=15)` — from the previous tick, not wall-clock aligned |
| `Cron(..., window=)` | `window=Window(weekdays=MONDAY_TO_FRIDAY, between=("08:00", "18:00"))` |
| `Cron(..., calendar=)` | `calendar=bolivia_holidays` — a port: `is_business_day(date) -> bool` |

A timezone is required for `Cron`: a schedule without one is refused. DST is evaluated in that
zone — a tick in a spring-forward gap runs at the next valid instant, a repeated hour runs once.

## What a tick sends

A tick can send a fixed Command, or first work out what to send. Four shapes, from the simplest:

**1. The Command, as is.** Only `scheduled_for` is filled.

**2. With input and context.**

```python
billing.schedule(
    CommandSendAgingReport,
    Cron("0 8 * * MON", timezone="America/La_Paz"),
    name="aging-report",
    input={"format": "pdf"},                       # the other fields of the Command
    context={"channel": "email"},                  # what the execution sees in self.context
)
```

**3. Planned: read the database, then decide what to send.** The tick executes a *planning*
Command. Its Feature is an ordinary use case — it reads through its own repository, with its own
span and tests — and answers the Commands to dispatch, each with its own context:

```python
class CommandPlanPaymentReminders(DataTransferObject):
    scheduled_for: datetime


@billing.feature(CommandPlanPaymentReminders)
class PlanPaymentReminders(Feature):
    def execute(self, dto: CommandPlanPaymentReminders) -> Dispatches:
        overdue = self.repository.fetch_all(Invoices, overdue_on(dto.scheduled_for))
        return Dispatches(
            Dispatch(
                CommandSendPaymentReminder(invoice_id=invoice.id),
                context={"tenant": invoice.tenant_id},
                key=invoice.id,                    # one run per invoice per tick
            )
            for invoice in overdue.items
        )


billing.schedule(
    CommandPlanPaymentReminders,
    Cron("0 9 * * *", timezone="America/La_Paz"),
    name="payment-reminders",
)
```

The gateway executes every `Dispatch` through the same guard — `(name, scheduled_for, key)` —
so a re-run of the tick never sends a reminder twice, and each one fails, retries and is reported
on its own. Planning stays a use case on the bus: no function that takes the bus, no query outside
a Feature.

**4. Schedules that come from data.** When a tenant chooses the hour — "send my aging report at
07:00" — the schedule is a definition (PRD_06), not code:

```python
billing.schedules_from(tenant_schedules)          # a ScheduleSource; re-read on every generation
```

`plan()`, `status()` and `describe_schedules()` show code-declared and data-defined schedules
alike, each saying where it came from.

## Later: one Command, one moment

Not every future execution repeats. "Send this reminder in three days", "expire this quotation at
its due date" is one Command at one moment, programmed from inside a use case:

```python
class CreateQuotation(Feature):
    scheduler: Scheduler                           # injected like any other dependency

    def execute(self, dto: CommandCreateQuotation) -> ResponseCreateQuotation:
        quotation = ...
        self.scheduler.later(CommandExpireQuotation(quotation_id=quotation.id), at=quotation.due_at)
        return ...
```

- `later(dto, at=…)` or `later(dto, after=timedelta(days=3))`, and `cancel(key)`.
- **Durable by default**: stored in a `scheduled_command` table in the same transaction as the use
  case that asked for it, so a rollback also cancels it and a restart does not lose it. A worker
  claims due rows with `SKIP LOCKED`.
- **In a thread only in development**: `InProcessClock` keeps them in memory; a process restart
  loses them, which is why it is not the production default.
- Providers map it natively where they can: Celery `eta`, Temporal start delay, DBOS enqueue.

## Policies

| Policy | Values | Default |
|---|---|---|
| `overlap` — the previous run is still going | `SKIP`, `QUEUE_ONE`, `ALLOW` | `SKIP` |
| `missed` — ticks that passed while nothing ran | `SKIP`, `RUN_LATEST`, `RUN_ALL` (within `missed_window`) | `RUN_LATEST` |
| `missed_window` | a `timedelta` | 1 day |
| `jitter` | seconds added at random, to spread load | 0 |
| `timeout` | a run longer than this is reported as failed | none |

## One run per tick

Every tick, **whatever the provider**, passes through one guard before the DTO is built:

```
schedule_run(name, scheduled_for, started_at, finished_at, outcome)   UNIQUE(name, scheduled_for)
```

Every replica may tick; one `INSERT` wins, the others see the conflict and do nothing. No leader
election, no singleton pod, and a provider that fires twice (Celery beat with two beats, a
Kubernetes CronJob in its rare double-run) still runs the use case once. The same table answers
`missed` (which ticks never got a row), `overlap` (a row started and not finished) and
observability (the last success). `scheduled_for` is also on the DTO, so a use case that must be
idempotent has its key.

## Providers

```python
class Clock(Protocol):
    def sync(self, schedules: Sequence[Schedule]) -> None: ...   # the catalog, idempotently
    def run(self, fire: Callable[[Schedule, datetime], None]) -> None: ...
```

| Provider | Runs where | For |
|---|---|---|
| `InProcessClock` | a thread in this process | development and tests |
| `PostgresClock(database)` | every replica ticks; the guard dedups | **production default** — nothing new to operate |
| `CeleryClock(app)` | beat fires, a worker executes | projects already on Celery |
| `TemporalClock(client)` | a Temporal Schedule; its one activity executes the DTO | projects already on Temporal |
| `DBOSClock()` | `DBOS.create_schedule` over the same Postgres | durable execution with no extra service |
| `kubernetes_cronjobs(gateway)` | writes CronJob manifests; each Pod runs one tick | K8s-native scheduling; **generates, never applies** |

A provider only decides *when to call* `fire`. Building the DTO, the guard, the context, the
trace and the report are the gateway's, identical for all of them.

## What a tick does

1. Claim `(name, scheduled_for)` in `schedule_run`; if another replica has it, stop.
2. Build the DTO with `scheduled_for` (other fields from the schedule's `input=`, if given).
3. Execute it on its bus inside `context({"schedule": name, "scheduled_for": ...})` and a root span
   `schedule.<name>` with `lag` (started − scheduled) as an attribute.
4. Final: record the outcome. A failure is reported like any other — once, with `failed_in` and
   `error_at` (PRD failure reporting).

## Seeing it

```python
gateway.plan(until=datetime(2026, 10, 1))    # the next ticks of every schedule, before running
billing.describe_schedules()                  # what is declared, with its policies
gateway.status()                              # per schedule: last run, last success, next tick, lag
```

"No success since X" per schedule is the alert that catches a clock that silently stopped.

## Testing

```python
from sincpro_framework.testing import ManualClock

clock = ManualClock(start=datetime(2026, 9, 26, 1, 59, tzinfo=LA_PAZ))
gateway = SchedulerGateway({"billing": billing}, provider=clock)
clock.advance(minutes=2)                      # 02:01 — fires close-cash-registers once
```

## Phases

1. `billing.schedule(...)` with `input` / `context`, `Cron` / `Every`, policies,
   `SchedulerGateway`, `schedule_run` guard, `InProcessClock`, `PostgresClock`, `ManualClock`,
   `plan()` / `status()`.
2. Planned ticks (`Dispatches`), `scheduler.later(...)` with the durable `scheduled_command` table.
3. `CeleryClock`, `TemporalClock`, `kubernetes_cronjobs`.
4. `DBOSClock`; windows and calendars; `schedules_from(...)` for schedules defined as data (PRD_06).

## Open questions

- Where does `schedule_run` live when a service has several databases — one per bus, or one for
  the gateway?
- Is `Every(...)` needed, or is `Cron` enough?
