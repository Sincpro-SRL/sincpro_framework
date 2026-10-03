# PRD_20: Durable workflows — the true outcome of every execution, steps that run once, and undo you compose

- **Status**: proposal, consolidated from the design conversation of 2026-10-02 — to validate
  before anything below is built. Name of the package still open (§9).
- **Replaces**: the first shape of this PRD — a `Saga` entity with `@starts` / `@handles` and its
  commands kept on its own row — built, never committed, and removed. What it proved stays in §5:
  a versioned record, a claim with a lease, retries through the relay's failure policies, undo
  newest first.
- **Depends on**: the bus (`bus.py`: interceptors inside the handler's `try`, error handlers
  outside it), the context manager (propagation across buses), `remote_execution` (the request
  context as gRPC metadata, errors raised as themselves), `event_driven` (Publisher, Subscriber,
  relay, failure policies), `caching` (`KeyValueStore`, `Idempotency`), `cron` (`Crons`,
  `CronProcess`, `KeyValueRuns`).
- **Philosophy**: explicit code, the framework keeping what code alone cannot — what really
  happened, what was already done, what is owed, where a run stands. A Feature never learns that
  a workflow exists.

## 1. What we concluded

| # | The idea | What it becomes |
|---|---|---|
| 1 | A failure should be a clean event | `ExecutionFailed`, published from the true outcome of an execution — fire and forget, never in the caller's way (§3) |
| 2 | Interceptors are the channel to whatever listens — a queue, a store, the same process | An **observer**: a read-only interceptor kind that sees every execution's true outcome, before any error handler can change it (§2) |
| 3 | Temporal's style | `@workflow` with a `run()` read top to bottom, `@task`s that run once per run, a journal, a worker — without a server of its own (§5) |
| 4 | A workflow has context and dependencies so it can undo; undo is composed, sometimes by logic | Dependencies injected like `Crons`; undo per task, per command in the context that owns it, or by hand from the journal (§6) |

## 2. The true outcome of an execution

What the code does today (`bus.py`):

```
FeatureBus.execute(dto)
  try:   run_through(interceptors, feature.execute, dto)     interceptors run INSIDE the try
  except error:
      observability.failed(...)                               always
      if an error handler:  answer = handle_error(error)      the failure becomes an answer
                            return answer                     the caller sees a success
      raise
```

**The gap.** An error handler may turn a failure into an ordinary response. Whoever called the
bus — a workflow's task, another context — cannot tell, and would never undo. Only observability
knows, and it keeps it to itself.

**What already sees the truth: an interceptor.** It runs inside the `try`: it sees the response
when it succeeds and the raw exception when it fails, before the handler answers.

**Proposal — an observer**, registered like an interceptor, that may only look:

```python
@billing.observer()                                   # every use case of the bus
def outcome(execution: Execution) -> None: ...        # called after; never raises into the call

class Execution(DataTransferObject):                   # what really happened, once per execution
    use_case: str                                      # the DTO's registered name
    dto: dict[str, Any]
    outcome: ExecutionOutcome                          # DONE · FAILED · ANSWERED_BY_HANDLER
    response: dict[str, Any] | None
    error_type: str | None
    error: str | None
    context: dict[str, Any]                            # correlation_id, tenant, user, a workflow run…
    bus: str
    layer: str                                         # feature · application_service
    started_at: datetime
    duration_ms: float
```

An observer cannot veto, adjust or answer — those stay an interceptor's — and an error it raises
is logged, never propagated: looking must never break the execution it looks at. It is the one
seam every component that needs the truth plugs into: the failure event (§3), a workflow's journal
(§5), an audit trail, a metric.

## 3. `ExecutionFailed`: the failure as an event

```python
@dataclass(kw_only=True)
class ExecutionFailed(DeliverableEventMixin, DomainEvent):
    name = "sincpro.execution.v1.failed"
    use_case: str = ""
    dto: dict[str, Any] = field(default_factory=dict)       # the original DTO
    error_type: str = ""
    error: str = ""
    answered_by_handler: bool = False
    context: dict[str, Any] = field(default_factory=dict)
```

- **Published by the framework**, from an observer: a Feature still raises, as today. A context
  whose rejection is part of its language (`InvoiceRefused`) keeps publishing it — the two say
  different things: one is the domain, the other the machinery.
- **Fire and forget.** Into a `Queue` the project names — `SyncQueue`, `BackgroundQueue`, a broker
  topic: that topic *is* the error queue — or through `RepositoryQueue` into the context's event
  table and the relay, when a failure must not be lost. Publishing it never blocks and never raises
  into the caller.
- **One line to switch on**, per bus: `billing.publish_failures(to=publisher)`. Nothing is wired by
  default (as every queue in the framework); whether it should be is decision §9.2.

**As built** (`sincpro_framework.outcomes`, `docs/core/outcomes.md`): a plain `DomainEvent`, emitted
where the exception escapes the call and never when an error handler answered (so no
`answered_by_handler`); it names the execution (`execution_id`, `causation_id`, `correlation_id`)
and classifies the failure as every wire does — `kind` (`refined_failure_kind`) and `retry_after`.

## 4. Across machines

Every bounded context may run on its own machine. What travels today:

| Path | The context of the execution | The outcome the caller sees |
|---|---|---|
| Same process, bus → bus | ✅ propagated | the exception, or what an error handler answered |
| `remote_execution` (gRPC/HTTP) | ✅ as metadata (`sp-request-context-bin`) | the remote exception raised as itself; `ContextTimeout` — **unknown whether it ran**; `ContextUnavailable` — it did not run |
| Broker (FastStream) | ✅ as headers (`sincpro-context`, W3C baggage, the identity headers — PRD_22) | none — publishing is fire and forget |

Three consequences:

1. **The truth of a remote execution lives on the remote machine.** Its observer is the one that
   sees it; it writes to a **store both machines share**, keyed by what the context carried (the
   run and the task). That store is the only place the caller's workflow and the callee's truth
   meet.
2. **A broker carries the context** — `FastStreamQueue` writes it as headers (`inject`) and the
   consumer opens it again (`extract`), the way `remote_execution` does (PRD_22).
3. **An unknown outcome is a state of its own.** `ContextTimeout`, or a process that died between
   starting a task and recording its end, is `UNCERTAIN` (§6).

```
 machine A — sales                       machine B — billing              machine C — warehouse
 ┌────────────────────────┐   gRPC       ┌──────────────────────┐          ┌──────────────────┐
 │ Workflows("sales")     │ ───────────► │ bus + observer ──────┼──┐       │ bus + observer ──┼──┐
 │  ConfirmSale.run()     │  context:    └──────────────────────┘  │       └──────────────────┘  │
 │  @task issue_invoice ──┼─ run, task                             ▼                             ▼
 │  WorkflowWorker        │ ◄──────────────── shared journal store (Redis/Valkey · a database) ──┘
 └────────────────────────┘     events (broker, with context) ─► starts_on / wait_for
```

## 5. Workflows: Temporal's style, the framework's shape

The shape is `Crons`': a registry per context, dependencies injected as typed attributes, a class
with one method, its own process when wanted.

```python
sales_workflows = Workflows("sales", journal=KeyValueJournal(RedisKeyValue(client)))
sales_workflows.add_dependency("billing", billing)          # local or remote: the same bus(dto)
sales_workflows.add_dependency("warehouse", warehouse)
sales_workflows.add_dependency("delivery", delivery)


@sales_workflows.workflow("sales.confirm_sale.v1", starts_on=SaleConfirmed)
class ConfirmSale(Workflow):
    billing: UseFramework
    warehouse: UseFramework
    delivery: UseFramework

    @task(retries=3)
    def issue_invoice(self, sale: SaleConfirmed) -> ResponseIssueInvoice:
        return self.billing(CommandIssueInvoice(order_id=sale.order_id), ResponseIssueInvoice)

    @issue_invoice.undo
    def cancel_invoice(self, sale: SaleConfirmed, answer: ResponseIssueInvoice | None) -> None:
        if answer is None:                                    # UNCERTAIN: undo from the input alone
            self.billing(CommandCancelInvoiceOfOrder(order_id=sale.order_id))
        else:
            self.billing(CommandCancelInvoice(invoice_id=answer.invoice_id))

    @task
    def reserve_stock(self, sale: SaleConfirmed) -> ResponseReserveStock:
        return self.warehouse(CommandReserveStock(order_id=sale.order_id), ResponseReserveStock)

    @reserve_stock.undo
    def release_stock(self, sale: SaleConfirmed, answer: ResponseReserveStock) -> None:
        self.warehouse(CommandReleaseStock(reservation_id=answer.reservation_id))

    def run(self, sale: SaleConfirmed) -> None:
        self.issue_invoice(sale)
        self.reserve_stock(sale)
        self.delivery(CommandRequestDelivery(order_id=sale.order_id))
        outcome = self.wait_for(DeliveryScheduled, DeliveryRefused, timeout=timedelta(hours=2))
        if isinstance(outcome, DeliveryRefused):
            self.revert("delivery refused")


WorkflowWorker(build_sales_workflows).start()       # its own process, like CronProcess
```

| Piece | What it does |
|---|---|
| `@task` | Runs **once per run**: its answer is journaled; a resumed run gets the answer back instead of running it again. Retries with backoff (the relay's policies). Its outcome is the observer's truth, not the return value an error handler made up |
| `run()` | The flow, read top to bottom. Re-run from the top on every resume, so it **decides and never acts** — the one rule: anything with an effect is a task |
| `wait_for(*events, timeout=)` | Suspends the run until one of them arrives with the run's correlation; nothing holds a thread. A durable timer is the same with no event |
| `revert(reason)` | The undo of every completed task, newest first. An exception escaping `run()` does the same |
| The journal | Append-only, per run: tasks started and ended, executions the observers saw inside them, events heard, undos. It *is* the run's state — locals are rebuilt from it |
| `self.state` | A DTO of the workflow's own, kept with the run, for what is neither an input nor an answer |
| `WorkflowWorker` / `run_due()` | Resumes suspended runs whose event came or whose time passed, retries, finishes what a dead worker left — with a lease, one worker per run |

**Started by**: `starts_on=` an event; `sales_workflows.start(name, dto)`; `CommandStartWorkflow`
on a bus, so REST/RPC/MCP and other services start one with `bus(dto)`; by hand.

**The journal store.** `KeyValueStore` has no compare-and-set, but `add` is atomic: an append-only
journal written at `wf:{run}:{n}` lets exactly one writer win each position, and a lease is an `add`
with a TTL — the pattern `KeyValueRuns` already uses for crons. So Redis/Valkey work through the
port that exists; in memory for tests; a Repository-backed journal is an optional adapter in `orm/`.

**Versioned names.** A run finishes on the definition it started with (`….v1`); a changed flow is
`….v2`. Without it, a deploy breaks the runs in flight.

## 6. Undo, composed

**Every task in a run has one of five states**, and the state says whether it is undone:

| State | Journal | Undone? |
|---|---|---|
| never ran | nothing | no |
| `FAILED` | started + error | no — its local transaction rolled back; it is what triggers the undo |
| `REFUSED` | a declared refusal event arrived for it | no |
| `DONE` | started + answer | yes, with its answer |
| `UNCERTAIN` | started, no end (`ContextTimeout`, a dead worker) | only if its undo accepts `answer: X \| None`; otherwise the run is `FAILED` for a person — read off the annotation when the workflow is added |

**Three places an undo can come from**, the most specific winning:

1. **The task's `.undo`** — next to the step, in the workflow.
2. **The command's owner**, declared once in its own context and reused by every workflow:
   `@billing.undo def issue_invoice(done: CommandIssueInvoice, answer: ResponseIssueInvoice | None) -> CommandCancelInvoice`.
   Also what undoes an effect the observer journaled *inside* a task (a nested command).
3. **By hand**, reading the journal: `for entry in reversed(self.journal.done()): …` — undo by
   logic, skip what must stay, reorder what depends on what.

A revert outside any workflow — a person cancelling by hand — is just another workflow
(`RevertSale`) or a Command: nothing compensates on its own unless a run is in play.

**Undo is a Command like any other**, idempotent: it runs at least once, carrying
`{run}:{task}` as an idempotency key in the context for `Idempotency` on the receiving side; one
that keeps failing leaves the run `FAILED` with its reason, until `retry(run)`.

## 7. What we could be forgetting

| Risk | Where it bites | Answer |
|---|---|---|
| Error handlers hide failures | a task that "succeeded" never undone | the observer (§2) — the outcome is read there, never from the return value |
| The broker drops the context | `wait_for` and observers across a broker lose the run | Phase 2 |
| Side effects outside a task | re-run on every resume | the one rule, a warning when `run()` executes a bus directly, a test helper that replays |
| A Feature half-done (an external call, then a raise) | `FAILED` says nothing happened | the Feature's responsibility: atomic, or the external call is its own task with its own undo |
| No isolation between runs | another request sees stock reserved, later released | documented countermeasures (semantic lock, commutative updates, reread value); an add-on if ever needed |
| Non-idempotent commands | duplicated invoices on retry | idempotency key in the context; `Idempotency` on the receiver |
| Poison runs | a run failing forever | retries spent → `FAILED`, visible, `ExecutionFailed` published |
| Journal growth | observed nested effects on hot paths | journal nested executions only when an undo is declared for them (decision §9.3), retention per run |
| Definitions changing under running runs | broken resumes | versioned names; refuse to resume a run whose definition is gone, saying so |

## 8. Roadmap

Ordered by what everything else stands on. Each phase is approved on its own.

| Phase | What | Touches | Why first |
|---|---|---|---|
| **0 — the true outcome** — **dropped** | an observer reading the outcome before an error handler | — | decided against: an error handler that answers decides the failure does not travel, and an interceptor already sees the raw outcome when a component needs it |
| **1 — failure as an event** — **built** | `ExecutionFailed` (`sincpro_framework.outcomes`), emitted once where an exception escapes the call and never when an error handler answered; `@bus.on_failure`, `failures.subscribe`, `bus.publish_failures(to=…)`; the exception carries `failure_id` — `docs/core/outcomes.md`; beside it `ExecutionCompleted` for every use case that answered (DTO and response), `@bus.on_completion`, `bus.publish_completions(to=…)` | `bus.py`, `use_bus`, `outcomes.py` | the clean form; useful alone (alerts, audit, n8n) |
| **2 — context on the wire** — **built** (PRD_22) | `FastStreamQueue` carries the execution context as headers, the consumer restores it (`context.adapters.propagation`) | `event_driven/adapters/faststream`, `entrypoints/faststream` | runs across machines need it |
| **3 — the engine** | `Workflows`, `@workflow`, `@task`, `run()`, the journal (memory + `KeyValueJournal`), `WorkflowWorker`, `run_due`, `start`/`retry`, versioned names | new package, `cron` (worker beside `CronProcess`) | Temporal's style on the framework's shape |
| **4 — undo** | `.undo`, `@bus.undo`, `revert()`, the five states, `UNCERTAIN` read off the annotations, idempotency keys | the new package, `use_bus` (`@bus.undo`) | the reason the whole thing exists |
| **5 — events and time** | `starts_on`, `wait_for`, durable timers, `CommandStartWorkflow` on a bus | the new package | the event-driven style |
| **6 — observability** | a span per run and per task, the journal as a timeline, `draw()` with the state of each task; metrics only those approved | `observability` | seeing a run is half of operating it |
| **7 — adoption** | test helpers (replay, fake buses), docs that run, the skill, proving it in `sincpro_synthesis` | `testing`, docs, skills | a component is trusted once it runs there |
| later | a Repository journal in `orm/`; JSON workflows over the same engine; exact recording through the context's outbox | — | when a case asks |

## 9. Decisions open

1. **The name.** `workflows` already holds the JSON compositions; this could become `workflows`
   with the JSON a way to define one later, or a new package (`durable`, `processes`).
2. **`ExecutionFailed` on by default, or one line per bus?** The framework wires nothing by default
   today.
3. **Journal nested executions always, or only those with a declared undo?**
4. **The failure event: a `DeliverableEventMixin` by default** (it can go through the outbox), or a
   plain event?
5. **What counts as a definitive failure** in an asynchronous step: a refusal (`ContractViolation`)
   at once, a transient error after its retries — or something else.
