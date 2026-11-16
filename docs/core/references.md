# References: a record of any bounded context, named by one text

A payment, a webhook, a job, a notification: something leaves now and comes back later, maybe in
another process or another service. It needs to say *which record* it is about, in a way that
survives being stored outside the code, and whoever receives it needs to read that record without
knowing which bus or which route answers it.

```python
from sincpro_framework.references import EntityRef, EntityRefs

ref = EntityRef.of(invoice)                       # in a use case of billing
str(ref)                                          # "billing.Invoice:0192…"

provider.charge(amount, metadata={"subject": str(ref)})      # it travels as text
```

```python
class CommandPaymentConfirmed(DataTransferObject):
    subject: EntityRef                            # a text on the wire, checked on the way in


@payments.feature(CommandPaymentConfirmed)
class ConfirmPayment(Feature):
    def execute(self, dto: CommandPaymentConfirmed) -> None:
        invoice = self.refs.resolve(dto.subject)  # billing's own Get, under its guards
        ...
```

## The ref

`bounded_context.Entity:key` — the bounded context and the entity as a `Reference` names a
relation (`auth.Tenant`), and the key `Get` finds the record by (`DEFAULT_GET_ID`, its identity by
default).

| | |
|---|---|
| `EntityRef.of(record)` | the ref of a record, in the bounded context that runs now; `EntityRef.of(record, bounded_context="billing")` outside any bus |
| `EntityRef.parse(text)` | the ref a text writes; a text that is not one is a `ValueError` — on a DTO field, a `422` |
| `str(ref)` | its text; on the wire a ref is always its text, published as `{"type": "string", "format": "entity-ref"}` |

A ref names the entity by its class's name, as the contract does: renaming the class renames its
refs, its contract and its relations alike.

## Resolving: always the record's own `Get`

```python
refs = EntityRefs([billing, members])             # the bounded contexts it may read
payments.add_dependency("refs", refs)

refs.resolve(ref)                                 # the record, or AggregateNotFound
refs.resolve_many([one, other, gone])             # [Invoice(…), Member(…), None], in order
```

- **Never past the record's read.** A ref is answered by the `Get` its bounded context registered
  for the entity — its interceptors, its answer — and **its guards decide anew** for whoever is
  calling, as for a request from outside. A use case that resolves never lends its own permission:
  a `Get` that requires `billing.invoice.read` refuses a caller that lacks it, and a `Get` that
  declares nothing on a strict bus is refused as it would be from outside.
- **A background caller has an identity too.** A webhook or a job resolves as the identity it runs
  under — `security.as_system("payment webhook")`, or a service token — and is refused like anyone
  else when the record's reads do not admit it.
- **Many at once read each entity once**: its `GetMany` when the bounded context registered one,
  else its `Get` per ref. A record that is not there is `None`; a refusal is raised.
- **What cannot be resolved says why**: a bounded context it was not given, or an entity its bus
  reads by no `Get` — `AggregateNotFound`, naming what is missing.

## Published: `Resolve`

The same read as a use case every wire publishes — for a screen that shows what an event points
at, or an agent that follows a ref:

```python
from sincpro_framework.references import EntityRefResolution, Resolve


class QueryResolve(Resolve):
    pass


@platform.feature(QueryResolve)
@rest.get("/refs")
@mcp()
@auth.authenticated
class Resolution(EntityRefResolution):            # reads the dependency named `refs`
    pass
```

```
GET /platform/refs?refs=["billing.Invoice:0192…","members.Member:0193…"]
->  {"records": [{"ref": "billing.Invoice:0192…", "display": "F-0042", "record": {…}},
                 {"ref": "members.Member:0193…", "display": "Ana Pérez", "record": {…}}]}
```

A `Query` like any other, registered on the bus the project chooses and guarded as it declares;
every record behind it still goes through its own `Get`, so publishing it opens nothing a caller
could not read already. `display` is the record as a person names it (`DEFAULT_DISPLAY`): what a
link shows. At most 500 refs per request.

## Extending

- `EntityRefResolution.resolver()` — the `EntityRefs` it reads with; override it when the
  dependency is named otherwise.
- `EntityRefs.reads(ref)` — which bus, `Get` and `GetMany` answer a ref; override it to read an
  entity by another read of the same context.

## Why this shape

- **One text, not a class per entity.** What travels outside the code can only be a text; the
  same form the contract already names relations with means nothing new to learn.
- **Reads, never writes.** A ref says *which record*; doing something to it is a Command of the
  context that owns it.
- **The record's own `Get`, never the repository.** A resolver that read stores directly would be
  a second door to every record, with none of the guards the context declared. Going through the
  bus keeps one door per record, whoever knocks.
