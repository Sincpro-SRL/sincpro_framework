# The persistence manifesto

What every persistence feature of the framework is held to, before it is designed and when it is
reviewed. It is short on purpose: a rule that needs a page of exceptions is not a rule. The
[decisions](decisions.md) say why each piece is the way it is; this page says what no piece may
break.

The rules are written for a service that moves money. A service that does not still gets them,
because a ledger is only the case where breaking one is noticed first.

## I. The port and the type

**1. The concrete repository is what a Feature types.** A Feature declares
`repository: Repository` from `sincpro_framework.data_layer.orm` and gets the full surface in its editor —
`context()`, its options, `savepoint`, the passthrough. Nobody downcasts, nobody types against a
narrower class to look portable.

**2. The port is the minimum every store can honour, never less and never more.** The abstract
`Repository` in `sincpro_framework.ddd` is the contract a relational store, a document store and
memory all keep with the same meaning. It is not a poor copy of the concrete class: it is the
part of it that is true everywhere.

**3. A word reaches the port only when it passes three questions.**

1. Does it mean the same thing in a relational store, a document store and in memory?
2. Can `MemoryRepository` implement it — or refuse it with `ContractViolation` when honouring
   it needs a transaction it does not have?
3. Does a Feature written against it behave the same on every store that accepts it?

Three yeses: it goes on the port. Any no: it stays on the concrete class, fully typed there. An
abstraction that only one store can keep is a second signature that drifts from the first.

**4. The concrete class is a subtype of the port.** It inherits it, so the type checker holds
every override to the port's signature: the concrete class may add parameters with defaults and
methods of its own, and may never narrow what the port accepts or widen what it answers.

**5. The double is the second implementation.** `MemoryRepository` exists so the port is
implemented twice. A word it silently ignores would let a Feature pass its tests and lose in
production, so it honours every word of the port or refuses it — never swallows it.

## II. Writing

**6. One door per write, explicit.** `save`, `remove`, `archive`, and the named bulk verbs. A
write happens because a Feature called one, never because an object was touched or a block
ended. No Active Record: the aggregate never knows its table.

**7. The aggregate is written whole.** Saving a root writes the children it holds, in the same
flush, through the same rules, and an assignment settles the ones it read and dropped. A child
that is another aggregate is declared a reference (`owned=False`) and is never written by its
parent; a to-many the tables infer is the root's until it is declared otherwise.

**8. Safe before complete.** The framework acts only on what it can see whole. A relation nobody
read is not empty, and a page is not the collection: neither is ever read as "delete the rest".
The one outcome that cannot be taken back — a silent delete — is never a default. Neither is a silent detach:
what a dropped child becomes is declared on the relation, and refused until it is.

**9. Every write is checked against what was read.** A loaded aggregate carries its `version`; a
write over a newer one is refused (`StaleAggregate`), never merged and never last-writer-wins.
A verb that does overwrite (an upsert, a write by criteria) says so in its name and its first
line.

**10. Facilitate, do not enforce — except where it is correctness.** A word the store cannot
honour is refused when honouring it is correctness (an isolation level, a lock) and warned about
when it only costs time (a timeout). Nothing is refused because it *might* be misused.

## III. Transactions

**11. The unit of work is the transaction.** `repository.context()` commits everything inside it
or nothing. Nesting joins the outer one; a `savepoint` is the only part that can fail alone.

**12. The transaction is configured where it begins.** Isolation, read-only and timeout are
options of `context()`, not of the process. A Feature that needs `serializable` says so in the
line that opens its unit of work.

**13. Nothing leaves before the commit.** A fact is published, a message sent, a cache let go of
only after the transaction that caused it committed — through the outbox or an after-commit hook
of that transaction. A hook that publishes inside a commit publishes what a rollback undoes.

**14. Errors name the fact.** The engine's error is translated into what happened — a duplicate,
a broken rule of the table, a conflict between transactions, a bound the caller set — in the
`ddd` vocabulary, carrying the engine's message. A driver exception never reaches a Feature, and two
different facts never share one name.

**15. A race is retried by reading again.** `retrying` re-runs a callable that reads fresh, for
the races a fresh read resolves — a stale version, a serialization failure, a deadlock. Nothing
retries a write blindly.

## IV. Money and records

**16. Amounts are exact.** An amount is an integer in minor units or a `Decimal`, never a float,
in the aggregate, in the column and in the DTO.

**17. Invariants live on the aggregate, contention on the lock.** The rule that a balance never
goes negative is a method on the account. That two withdrawals do not both pass it is the unit of
work's job: `for_update` on the row, or `serializable` and `retrying`.

**18. A command that moves money runs once.** A retried request with the same key answers the
first result and does not move money again — the idempotency record committed in the same
transaction as the write ([caching](../caching/README.md)).

**19. What happened is kept.** Who wrote (`AuditedMixin`), what changed (`ChangeTrackingMixin`),
and the facts themselves when the history is the product (event sourcing). A record is archived,
not deleted, when anything may point at it.

## V. Distributed

**20. One transaction per database; no two-phase commit.** A bounded context owns its database.
Consistency across databases is eventual and built from three parts, each with its own guarantee:

| Part | Guarantee |
|---|---|
| **Outbox** — the fact saved in the same transaction as the change | the fact exists if and only if the change does |
| **Relay** — pending facts claimed with `for_update` + `skip_locked` | one replica delivers each fact |
| **Inbox + idempotent handler** — delivery at least once, effect once | a redelivery changes nothing |

At-least-once delivery plus an idempotent effect is the only "exactly once" this framework
promises ([shapes](../shapes.md), [brokers](../events/brokers.md)).

**21. The request's identity travels.** Correlation id, tenant and user cross every bus, queue
and process boundary with the call, so a fact delivered an hour later is traced to the request
that caused it.

**22. Scheduled work runs once per tick across replicas.** A cron claims its tick before it runs
([cron](../cron/README.md)).

**23. A process that spans contexts compensates; it never locks across them.** A long process is
a sequence of local transactions, each with the step that undoes it.

## VI. Orchestrated, never a cage

**24. The common case is orchestrated; every case is reachable.** What an enterprise service
needs — consistency, versions, scope, rules, traces — works by default, with nothing assembled by
hand. What the framework does not orchestrate, or orchestrates differently than a case needs, is
reachable underneath without asking: the model is a plain SQLAlchemy mapping, `unit.session` is
SQLAlchemy whole inside the same transaction, `statement()` hands a real `Select` out. A door
underneath that keeps the transaction is part of every feature, not an afterthought.

**25. The framework declares; SQLAlchemy executes.** The framework builds what SQLAlchemy does not
have — the query language, the page per parent, the scope, the hooks, the named errors, relations
to other contexts — and maps the rest onto what SQLAlchemy already does well. Mechanics rebuilt
beside a library that has them are a second implementation to keep in step with the first.

The use cases, from the simplest to the advanced, and where each is answered:
[use-cases.md](use-cases.md).

## Where each rule stands

| Rule | Status |
|---|---|
| 1–5, 7–12, 14–22 | built — the cascade, the transaction options, the named errors, `retrying`, `upsert` and the writes by criteria are [PRD_17](../prd/PRD_17_transactional-persistence.md) |
| 6 | built: the commit writes what was saved; `Writes.CHANGED` for a block that wants the session's tracking (decisions §25) |
| 13 | built: the outbox, and `after_commit` per unit of work |
| 23 | not built: workflows compose Commands, but have no compensating step |
| 24 | built: `unit.session`, `statement()` → `run()`, plain mappings |
| 25 | partial: the writing of relations is still rebuilt beside `relationship()` — the spike of PRD_18 |
