# The reads of one aggregate: `EntityReads`

A screen asks four things of an aggregate: one record, several records by key, the short
list a select shows for what the user typed, and a page by criteria. `EntityReads` answers the
four from what the entity answers about itself — its `DEFAULT_*` class methods
([Specification](specification.md)) — as one Feature registered for the DTOs a project names.

Nothing here is required. A Feature written by hand with `matching`, `detail_of` and
`repository.search` does the same; `EntityReads` is that Feature, written once.

## The shape

```python
from dataclasses import dataclass

from sincpro_framework.ddd import (
    TEXT,
    Any,
    Condition,
    Criteria,
    Entity,
    EntityReads,
    Get,
    GetMany,
    LiteralSearch,
    Operator,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    Search,
    Sort,
)


@dataclass
class Account(Entity):
    code: str
    name: str = ""

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
        return Criteria(where=Any(any=[
            Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT),
            Condition(field="name", operator=Operator.LIKE, value=TEXT),
        ]))


class ResponseAccount(ResponseRecord):
    account: Account


class ResponseAccounts(ResponseRecords):
    accounts: list[Account]


class ResponseListAccounts(ResponsePaginatedQuery):
    accounts: list[Account]


class QueryGetAccount(Get[Account, ResponseAccount]):
    pass


class QueryGetManyAccounts(GetMany[Account, ResponseAccounts]):
    pass


class QueryFindAccounts(LiteralSearch[Account, ResponseListAccounts]):
    pass


class QueryListAccounts(Search[Account, ResponseListAccounts]):
    pass


@accounting.feature(
    [QueryGetAccount, QueryGetManyAccounts, QueryFindAccounts, QueryListAccounts]
)
class AccountReads(EntityReads[Account]):
    pass
```

The base of each DTO names the entity and the response, so the Feature reads both off the DTO
and nothing is declared twice. The DTOs and the responses keep the names the project gives them:
nothing requires a prefix, and the records keep their own name (`account`, `accounts`), never
`items`.

## The reads

| DTO | Carries | Answers | When nothing is there |
|---|---|---|---|
| `Get[T, R]` | `id`, `criteria` | `R(ResponseRecord)`: the one record whose `DEFAULT_GET_ID` is `id`, as `DEFAULT_READING` brings it | `AggregateNotFound` |
| `GetMany[T, R]` | `ids`, `criteria` | `R(ResponseRecords)`: a list in the order of `ids`, each as `DEFAULT_READING` brings it | the absent ids in `missing` |
| `LiteralSearch[T, R]` | `text`, `criteria` | `R(ResponsePaginatedQuery)`: `DEFAULT_LITERAL_SEARCH` filled, a page of 8, identity and display | an empty page |
| `Search[T, R]` | `criteria` | `R(ResponsePaginatedQuery)`: the page asked, as `DEFAULT_READING` brings it, in `DEFAULT_ORDER` | an empty page |
| `DomainEvents[T, R]` | `id`, `names`, `criteria` | `R(ResponsePaginatedQuery)`: the record's events, oldest first, from the event class `R` holds | an empty page; `AggregateNotFound` when a key other than the identity finds no record |

### What the caller sends wins, part by part

What the entity answers is the default. The caller's `criteria` replaces each part it **names**
— `specification`, `order`, `pagination`, `count`… — and its `where` adds to the read's own
filter (the key for a `Get`, the template for a select). This is `Criteria.replaced_by`.

| The caller sends | It reads |
|---|---|
| nothing, or `Criteria()` | exactly what the entity answers |
| `Criteria(specification=...)` | that specification, even one asking for more than `DEFAULT_READING` |
| `Criteria(order=(Sort(field="name"),))` | in that order |
| `Criteria(where=active = true)` | the key **and** `active = true`: a `Get` of an inactive record is not found |

"Names" is what pydantic records as set (`model_fields_set`), not what differs from a default:
`Criteria(order=())` names the order on purpose. A scope merged with `merged_with` keeps only
what either side named, so a list that adds its tenant to the caller's criteria still reads
the entity's defaults:

```python
@accounting.feature(QueryListAccounts)
class ListAccounts(EntityReads[Account]):
    def search(self, dto: QueryListAccounts) -> ResponseListAccounts:
        scope = Criteria(where=Condition(field="tenant_id", value=dto.tenant_id))
        return super().search(dto.model_copy(update={"criteria": scope.merged_with(dto.criteria)}))
```

- **`GetMany` is a list, not a page.** No cursor and no count: every key asked is
  answered, or named in `missing`. A repeated key is answered once. An empty `ids` is an
  empty list.
- **`AggregateNotFound` is `not_found` on every wire**: 404 over REST, its own code over
  JSON-RPC, gRPC and MCP. An archived record, or one outside the scope the repository was
  narrowed to, is not found too.
- **`LiteralSearch` is the select.** The literal fills `DEFAULT_LITERAL_SEARCH`, and a blank
  literal is the first page in the entity's order (what the template asks besides the text
  still applies). The caller's `criteria` adds a filter or sets a page size.
- **`Search` is the list.** Each record comes as `DEFAULT_READING` brings it, in
  `DEFAULT_ORDER`, unless the criteria names its own. An entity whose reading is deep makes
  deep lists: a caller that wants a light one names a specification.

`Get` and `GetMany` read as a page filtered by the key, not through `repository.get`. That
way the definition (`entity_meta_data`) and the relations come back the same from every store,
and the answer is cut by the same specification a page is. A Feature that is about to change
the record reads it with `repository.get(id, detail=detail_of(Account))`, which hands back the
live aggregate.

### DomainEvents: a record's events

```python
class ResponseIssueEvents(ResponsePaginatedQuery):
    events: list[ProjectEvent]                  # the context's event class: where to read

class QueryIssueEvents(DomainEvents[Issue, ResponseIssueEvents]):
    pass

@project.feature([QueryGetIssue, QueryListIssues, QueryIssueEvents])
class IssueReads(EntityReads[Issue]):
    pass

project(QueryIssueEvents(id=issue_id))                             # every event, oldest first
project(QueryIssueEvents(id=issue_id, names=["project.issue.v1.closed"]))   # only those wire names
project(QueryIssueEvents(id=issue_id, criteria=Criteria(
    where=Condition(field="created_at", operator=Operator.GTE, value=start),
)))                                                                 # since a moment
```

- **On the record's own reads**, beside `Get`, `GetMany`, `LiteralSearch` and `Search`; whatever
  guards the entity's reads guards its history.
- **The type is the class the DTO names**, never what a caller sends: the events are filtered by
  `entity_type = "Issue"` and `entity_id = <identity>` together, so a `Sandbox` and a `Workspace`
  that share the id `dev-1` never mix.
- **`id` is the record's key**, as `Get` reads it. When `DEFAULT_GET_ID` names another field,
  the record is read first and its identity is what the events name. A blank `id` is refused
  (422): it would read every event about no record.
- **Where the events live** is the class the response holds: the context's base event, or a
  narrower class (`events: list[IssueClosed]`) to read only those. A response that does not hold
  a `DomainEvent` is refused, naming it.
- **`names`** keeps the events whose wire name is one of them (`name in names`); a single
  name works as a one-item list. A name no class records answers no rows — it is not checked
  against the classes imported, which may not be yet.
- **Oldest first** (`id`, a UUIDv7); the caller's criteria lays over it — its `where` adds, its
  `order` and `pagination` replace (`order=parse_order("-id")` for newest first).
- **Several records at once** — an issue with its runs — is `history_of(issue, *runs)`: the
  criteria a `Search` on the context's event class reads them with, each by its type and
  identity.

## Extending

| To | Do |
|---|---|
| Act before or after one read | Override `get`, `get_many`, `literal_search` or `search` and call `super()` |
| Read from a dependency not called `repository` | Override `reads_from()` and return it, typed |
| Act around every use case, from outside the class | An interceptor on the bus |
| Replace the whole Feature | `@accounting.feature(QueryGetAccount, replaces=AccountReads)` |

```python
@accounting.feature([QueryGetAccount, QueryFindAccounts])
class AccountReads(EntityReads[Account]):
    ledger: Repository
    audit: AuditTrail

    def reads_from(self) -> Repository:
        return self.ledger

    def get(self, dto: QueryGetAccount) -> ResponseAccount:
        answer = super().get(dto)
        self.audit.viewed(answer.account.id)
        return answer
```

One Feature answers any subset of the four. A DTO it does not answer is refused by name.

## Why this shape

- **Typed DTOs, not a generated resource.** A generic resource (`bus.expose(Account)`) would
  hide the DTOs that the catalog, the OpenAPI, MCP and the tests all name. Here the project
  names them, and every interceptor, permission and entrypoint treats them as any other DTO.
- **Two DTOs for one and for many, not one that changes shape.** An `id: str | list[str]`
  publishes a union response that every client has to branch on, and «not there» means 404 for
  one record and a `missing` list for many. Two DTOs keep each contract exact, and one
  implementation sits behind both.
- **Reads only.** A write in DDD carries an intent (`CommandConfirmInvoice`), not a shape to
  overwrite, so writes are not generated.
