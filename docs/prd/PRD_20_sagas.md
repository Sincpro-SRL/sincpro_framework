# PRD_20: Distributed transactions — rollbacks as an entrypoint, coordinated by signals

- **Status**: proposal, 2026-10-03 — the recipe agreed in conversation; nothing of §3–§6 built. Built
  and kept: the outcomes (`sincpro_framework.bus_pipeline.outcomes`: `ExecutionCompleted`, `ExecutionFailed` with
  `kind` and `retry_after`), the context on every wire (PRD_22), the execution identity (PRD_21).
- **Replaces**: two earlier shapes, both dropped — a `Saga` entity with `@starts` / `@handles`, and
  durable workflows in Temporal's style (`@workflow`, `@task`, a `run()` replayed from a journal).
  Neither kept a service unaware that it takes part.
- **Depends on**: **PRD_23** (the remote bus — its release gate), the outcomes, the context and its
  store, the identity, the context's event table and its relay (PRD_19), `Idempotency` (`caching`).
- **Philosophy**: a service never knows it is part of a distributed transaction. It declares, once
  and as an entrypoint, how what it does is undone. The framework records, on its own and in the same
  local transaction, what each service did; a coordinator signals the undo. Same code in one process,
  in several, or across services — only the queue and the context map change.

## 1. What we concluded

| # | Conclusion |
|---|---|
| 1 | A Feature never learns a transaction exists — not a decorator, not an argument, not a base class |
| 2 | The undo belongs to the context that owns the data: it runs there, atomic in its own database |
| 3 | How a context is undone is an **entrypoint** of that context — `Rollbacks`, beside `Crons` and its REST routes — a registry reached by name, never imported by the caller |
| 4 | Everything is signals: what was done, what failed, what to undo — events and commands, asynchronous whenever the queue is |
| 5 | The coordinator lives where the deployment puts it: the same process, a process of its own, a service |
| 6 | A transaction is baptized: its `transaction_id` travels in the context, as every key does |

## 2. The flow

```
                 transaction_id = T   (in the context — it travels by itself)
 sales ──────────────► billing:   issues the invoice  + records "in T: CommandIssueInvoice → F-9"
   │                                                   (the same local transaction as the invoice)
   │  ──────────────► warehouse: reserves stock       + records "in T: CommandReserveStock → R-3"
   │  ──────────────► delivery:  ✗ raises → ExecutionFailed, its context naming T
   │
 coordinator (hears that T failed, or is told to abort T)
   ├── RollBackTransaction(T) ──► warehouse  → its Rollbacks → CommandReleaseStock(R-3)
   └── RollBackTransaction(T) ──► billing    → its Rollbacks → CommandCancelInvoice(F-9)
                                  newest first; each undo atomic where its data is → "rolled back"
```

## 3. The pieces

### 3.1 `Rollbacks` — the entrypoint of each context

```python
# billing/entrypoints/rollbacks.py — beside its crons and its routes; the Features do not change
billing_rollbacks = Rollbacks(billing)

@billing_rollbacks.of(CommandIssueInvoice)
def issue_invoice(done: CommandIssueInvoice, answer: ResponseIssueInvoice) -> CommandCancelInvoice:
    return CommandCancelInvoice(invoice_id=answer.invoice_id)
```

- A registry: a command → how what it did is undone, as another command of the same context.
- Registering it adds one command to the bus, `RollBackTransaction(transaction_id)`, answered by the
  framework: it reads what this context recorded in that transaction and runs each undo, newest first.
- Undo logic that is not a mapping — skip what must stay, undo two steps with one command — is a
  function receiving what was recorded and returning the commands to run.

### 3.2 The record — automatic, atomic

When a command executes with a `transaction_id` in the context **and** the context declared a
rollback for it, the framework records what it did: the DTO, the response, the execution's identity,
the transaction. In the context's event table (PRD_19), in the **same local transaction** as the
command's own writes:

- a write rolled back locally leaves no record — nothing that did not happen is ever undone;
- the coordinator needs no parameters — `RollBackTransaction(T)` is all a context is told;
- "did it run?" has a definite answer where the data is: the record exists or it does not.

A context without a database records in its context store (`KeyValueContexts`) — at least once.

### 3.3 The baptism — where the transaction starts

```python
class ConfirmSale(ApplicationService):
    billing: UseFramework
    warehouse: UseFramework

    def execute(self, dto: CommandConfirmSale) -> None:
        with distributed_transaction("sales.confirm_sale", key=dto.order_id):
            self.billing(CommandIssueInvoice(order_id=dto.order_id), ResponseIssueInvoice)
            self.warehouse(CommandReserveStock(order_id=dto.order_id), ResponseReserveStock)
            self.delivery(CommandRequestDelivery(order_id=dto.order_id))
```

- Opens a scope whose context names `transaction_id` (and the transaction's name); every call inside
  carries it — local, remote (PRD_23) or through a queue.
- Leaving the block normally completes the transaction (`TransactionCompleted`); an exception leaving
  it aborts it (`TransactionAborted`) and is raised again. A step that answers later (a queue) is part
  of it while the transaction is open.

### 3.4 The coordinator

Hears the signals of a transaction and, on an abort, sends `RollBackTransaction(T)` to every context
that recorded something in it, newest first. Where it runs:

| Deployment | Coordinator | How `RollBackTransaction(T)` arrives |
|---|---|---|
| One process | in the process | the bus, directly |
| Several processes, one machine or several databases | in the process that baptized it, or a process of its own (as `CronProcess`) | the remote bus (PRD_23) |
| Microservices | a process or a service of its own | the remote bus, or a `TransactionAborted` on the broker that every `Rollbacks` listens to |

## 4. Signals

| Signal | Emitted by | Means |
|---|---|---|
| `ExecutionCompleted` / `ExecutionFailed` | every bus (built) | a use case answered / an exception escaped — its context names T |
| `StepRecorded` | a participant, after its local commit (the relay) | "in T, I did this" — how the coordinator knows who took part, and in which order |
| `TransactionCompleted` / `TransactionAborted` | the baptism (or a person, by hand) | T ended well / must be undone |
| `RollBackTransaction(T)` | the coordinator, a command | "undo what you did in T" |
| `TransactionRolledBack` / `RollbackFailed` | a participant | done / failed after its retries — for a person |

## 5. What we must not forget

| Risk | Answer |
|---|---|
| A record written before the local commit, then rolled back | written in the same transaction as the command (§3.2) |
| A step that answers after the abort started | rolled back when its `StepRecorded` arrives for an aborted T |
| An undo run twice (a retry, a duplicate signal) | a record is marked rolled back; `RollBackTransaction(T)` is idempotent |
| An undo that keeps failing | retries through the relay's policies, then `RollbackFailed` — visible, for a person |
| The coordinator dies mid-undo | the records stay; a recovery process (a cron) finishes what is pending |
| A remote step whose outcome is unknown (`UNKNOWN_OUTCOME`, PRD_23) | the participant's own record decides: if it ran, it is there |
| No isolation between transactions | documented: another request sees the invoice until it is cancelled — semantic locks are the project's |
| A transaction nobody completes | closed by a timeout of its own; its records kept for a retention period |

## 6. Decisions open

1. **How the coordinator learns who took part**: `StepRecorded` from each participant (order across
   services, recommended), or `TransactionAborted` broadcast to every `Rollbacks` (simpler, no order).
2. **The names**: `distributed_transaction`, `Rollbacks`, `RollBackTransaction` — or the vocabulary of
   the products.
3. **Retention** of the records of a completed transaction.

## 7. Phases

Each approved on its own, after PRD_23 R1–R4.

| Phase | What |
|---|---|
| **T1 — the entrypoint** | `Rollbacks`, `RollBackTransaction`, the record in the same local transaction; one process |
| **T2 — the baptism and the coordinator in process** | `distributed_transaction`, `TransactionCompleted` / `Aborted`, undo newest first |
| **T3 — across services** | `StepRecorded` through the relay, the coordinator in a process of its own, `RollBackTransaction` over the remote bus and over a broker |
| **T4 — recovery and visibility** | the recovery cron, `RollbackFailed`, a span per transaction, the docs that run, the skill, proved in `sincpro_synthesis` |
