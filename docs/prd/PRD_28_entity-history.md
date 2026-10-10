# PRD_28: an entity's domain events, read like its other reads

- **Status**: proposal approved 2026-10-09; built on `feat/entity-history`, not committed.
- **Found by**: Forge. Each context wrote one hand-made events Feature per aggregate
  (`ListWorkspaceEvents`, `ListIssueEvents`…), and after merging them, a `history_of` helper the
  caller assembles. Two aggregates of one context share ids (`dev-1` is a Sandbox and a
  Workspace), so a history filtered by `entity_id` alone mixes them.
- **Depends on**: PRD_19 (a `DomainEvent` is an entity; one event table per bounded context),
  PRD_27 (`EntityReads`, `DEFAULT_*`, `Criteria.replaced_by`), PRD_15 (one failure
  classification on every wire).

## 1. What a reader asks of an event log

| Question | Today | After |
|---|---|---|
| The history of one record | a hand-written Feature per aggregate, or a `Search` the caller filters by `entity_type` + `entity_id` | **`DomainEvents[Issue, R]`**, a read of the record's own `EntityReads`, `names` to keep some wire names |
| One event, several events | `Get` / `GetMany` on `EntityReads[ContextEvent]` | unchanged |
| The context's log: by event type, by aggregate type, by time, by flow | `Search` on `EntityReads[ContextEvent]` with a criteria on the envelope | unchanged, written down as recipes (§4) |
| Events about no record | stored with `entity_type=""`, readable by `Search` | unchanged; guidance on giving them a subject (§5) |

Only the first row needed something new.

## 2. The rule that decides the shape

**The type of the record is never what a caller sends.** It comes from the class the read is
declared on. Every mature log keys a record's history by type and id together — PaperTrail
(`item_type`, `item_id`), django-auditlog (`content_type`, `object_pk`), Odoo (`model`,
`res_id`), Debezium (`aggregatetype`, `aggregateid`), KurrentDB and Eventuous (stream
`{Type}-{id}`) — and the ones that let a caller choose the type alone, or drop it (Axon 4's
index on the id and the sequence only), depend on ids being globally unique. Ours are not
always: a project may key a record by a code (`DEFAULT_GET_ID`).

## 3. `DomainEvents`

```python
class ResponseIssueEvents(ResponsePaginatedQuery):
    events: list[ProjectEvent]                  # the context's event class: where to read

class QueryIssueEvents(DomainEvents[Issue, ResponseIssueEvents]):
    pass

@project.feature([QueryGetIssue, QueryListIssues, QueryIssueEvents])
class IssueReads(EntityReads[Issue]):
    pass

project(QueryIssueEvents(id=issue_id))                                  # every event, oldest first
project(QueryIssueEvents(id=issue_id, names=["project.issue.v1.closed"]))  # only those wire names
project(QueryIssueEvents(id=issue_id, criteria=Criteria(
    where=Condition(field="created_at", operator=Operator.GTE, value=start),
)))                                                                      # since a moment
```

- **Declared on the record's own reads**, beside `Get`, `GetMany`, `LiteralSearch` and
  `Search`: one record, several, a select, a page, and its history. Whatever guards the entity's
  reads guards its history.
- **`id` is the record's key** — `DEFAULT_GET_ID`, as `Get` reads it. When the key is the
  identity, the events are filtered by it directly; when it is another field, the record is
  read first (`AggregateNotFound` when there is none) and its identity is what the events name.
  A blank `id` is refused (422): it would read every event about no record.
- **Where the events live** is the event class the response holds (`list[ProjectEvent]`): the
  context's base, or a narrower class to read only those events. A response whose records are
  not a `DomainEvent` is refused, naming it.
- **The filter** is `entity_type = Issue.__name__` and `entity_id = <identity>` — what
  `Entity.record` stamps. The caller's criteria is laid over it with `replaced_by`: its `where`
  adds, its `order`, `pagination` and `specification` replace.
- **Oldest first** (`id` ascending, a UUIDv7): a history reads forward, as a stream's revision
  does. A caller asking for the newest first names `order=parse_order("-id")`.
- **A subclass of `Issue`** is read under its own name: a history is of the class that recorded.
- **The event name is a first-class filter.** `names: list[str]` (one name accepted, as a
  one-item list) keeps the events whose wire name is among them, ANDed with the subject and the
  caller's criteria. A name is not checked against the classes imported — a class may not be
  imported yet — so an unknown name answers no rows. `name` is filterable in every store: the
  memory store reads the class attribute, SQL the `name` column the event is mapped under.

## 4. The context's log: recipes, no new DTO

`EntityReads[ProjectEvent]` with `Get`, `GetMany` and `Search` already answers it; the envelope
is columns, so each question is a criteria:

| Question | Criteria |
|---|---|
| events of some types | `name in ["IssueOpened", "IssueClosed"]` — the general query; `DomainEvents(names=…)` is the same for one record |
| events of one kind of record | `entity_type = "Issue"` |
| events about no record | `entity_type = ""` |
| a time range | `created_at between [start, end]` |
| everything one request caused | `correlation_id = …` |
| several records at once | `any` of (`entity_type` and `entity_id`) pairs — `history_of(issue, *runs)` builds it |

`history_of(*records)` is the helper the framework gives for the last row: the criteria that
reads the histories of several records together, each by its type and identity.

## 5. Application events

An event about no record is first-class: same table, same reads. Before storing one with
`entity_type=""`, give it a subject when it has one — a closing day, an import, an execution —
so its history can be asked for: `ExecutionCompleted` and `ExecutionFailed` already name
`entity_type="Execution"`. `""` stays for what truly has no subject, read with
`entity_type = ""`. No sentinel subject is introduced (KurrentDB reserves `$` for its own
system streams; that vocabulary is not ours).

## 6. Not in scope

Publishing, relays, event sourcing; a log shared across contexts (each keeps its table and
its bus); line-level diffs of a record (`ChangeTrackingMixin` records them as events already).

## 7. Proof

| Case | Test |
|---|---|
| history of one record, oldest first, a caller's time filter adding to it, a caller's order replacing it | memory and SQL |
| `names` keeps those wire names, one name accepted, an unknown name answers nothing, combined with the subject and a time filter | memory and SQL |
| the context's log filtered by `name in [...]` | memory and SQL |
| two kinds of record with the same id do not mix | memory and SQL |
| a key other than the identity reads the record first; unknown key → `AggregateNotFound` | memory |
| blank id refused; response not holding events refused | memory |
| a narrower event class reads only those events | memory |
| `history_of` over several records | memory and SQL |
| through the bus, on the record's own `EntityReads` | memory |

## 8. Research (2026-10)

- Per-record history as a filter by type and id on one table: PaperTrail, django-auditlog
  (`get_for_object`, default `-timestamp`), Odoo `mail.message` (`model`, `res_id`;
  `addons/mail/models/mail_message.py`), Debezium outbox (`aggregatetype`, `aggregateid`).
  As a stream named `{Type}-{id}`: KurrentDB, Eventuous.
- Events with no aggregate: Axon 4 keeps them in the same table with no type ("standalone");
  Axon 5 and Marten move to tags (DCB); KurrentDB needs a stream for every event and reads by
  type through `$et-` and by category through `$ce-`; Temporal's subject is the execution.
- Reading a record inherits the record's permission; reading the whole log is a broader one
  (KurrentDB's `$all` is admin-only).
- Sources: docs.kurrent.io (reading events, system projections), martendb.io (querying, DCB),
  docs.axoniq.io 5.3 (event store internals), docs.spring.io/spring-modulith (events),
  debezium.io (outbox event router), paper_trail, django-simple-history, django-auditlog,
  temporalio/api, Odoo 18 source.
