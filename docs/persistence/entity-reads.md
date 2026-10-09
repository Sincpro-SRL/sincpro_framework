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

## The four reads

| DTO | Carries | Answers | When nothing is there |
|---|---|---|---|
| `Get[T, R]` | `id`, `criteria` | `R(ResponseRecord)`: the one record whose `DEFAULT_GET_ID` is `id`, as `DEFAULT_READING` brings it | `AggregateNotFound` |
| `GetMany[T, R]` | `ids`, `criteria` | `R(ResponseRecords)`: a list in the order of `ids`, each as `DEFAULT_READING` brings it | the absent ids in `missing` |
| `LiteralSearch[T, R]` | `text`, `criteria` | `R(ResponsePaginatedQuery)`: `DEFAULT_LITERAL_SEARCH` filled, a page of 8, identity and display | an empty page |
| `Search[T, R]` | `criteria` | `R(ResponsePaginatedQuery)`: the page asked, as `DEFAULT_READING` brings it, in `DEFAULT_ORDER` | an empty page |

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
way the definition (`model_meta_data`) and the relations come back the same from every store,
and the answer is cut by the same specification a page is. A Feature that is about to change
the record reads it with `repository.get(id, detail=detail_of(Account))`, which hands back the
live aggregate.

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
