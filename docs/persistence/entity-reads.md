# The reads of one aggregate: `EntityReads`

A screen asks four things of an aggregate: one record, several records by identity, the short
list a select shows for what the user typed, and a page by criteria. `EntityReads` answers the
four from what the entity declared in its `presentation` ([Specification](specification.md)),
as one Feature registered for the DTOs a project names.

Nothing here is required. A Feature written by hand with `matching`, `detail_of` and
`repository.search` does the same; `EntityReads` is that Feature, written once.

## The shape

```python
from dataclasses import dataclass

from sincpro_framework.ddd import (
    Entity,
    EntityReads,
    Get,
    GetMany,
    LiteralSearch,
    Match,
    Presentation,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    Search,
)


@dataclass
class Account(Entity):
    code: str
    name: str = ""

    presentation = Presentation["Account"](
        search=lambda a: [Match.equal(a.code), Match.prefix(a.code), Match.contains(a.name)],
        order=lambda a: a.code,
    )


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
| `Get[T, R]` | `id`, `criteria` | `R(ResponseRecord)`: the one record with the declared `detail` | `AggregateNotFound` |
| `GetMany[T, R]` | `ids`, `criteria` | `R(ResponseRecords)`: a list in the order of `ids`, each with the declared `detail` | the absent ids in `missing` |
| `LiteralSearch[T, R]` | `text`, `criteria` | `R(ResponsePaginatedQuery)`: a page of 8, identity and display | an empty page |
| `Search[T, R]` | `criteria` | `R(ResponsePaginatedQuery)`: the page asked, in the declared order | an empty page |

- **`Get` and `GetMany` start from the declared `detail`.** The caller's `criteria` merges on
  top, and its specification can only narrow it: a caller that names `code` gets the identity
  and `code`, never more than the detail.
- **`GetMany` is a list, not a page.** No cursor and no count: every identity asked is
  answered, or named in `missing`. A repeated identity is answered once. An empty `ids` is an
  empty list.
- **`AggregateNotFound` is `not_found` on every wire**: 404 over REST, its own code over
  JSON-RPC, gRPC and MCP. An archived record, or one outside the scope the repository was
  narrowed to, is not found too.
- **`LiteralSearch` is the select.** The literal matches as `presentation.search` says, and a
  blank literal is the first page in the declared order. The caller's `criteria` adds a filter
  or a page size.
- **`Search` is the list.** The criteria is the client's; the declared order applies when it
  names none.

`Get` and `GetMany` read as a page filtered by identity, not through `repository.get`. That
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
