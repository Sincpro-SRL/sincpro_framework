# Specification: what to bring back of each record

One field on `Criteria`, `specification`, says what is wanted OF each record: which scalars,
which relations, and how to bring each relation. It is the request contract for every read,
at the first level and at any depth below it.

```python
class Specification(RootModel[dict[str, Criteria]]): ...

class Criteria(DataTransferObject):
    where, order, pagination, grouping, count, meta      # as before
    specification: Specification | None = None           # field name → how to bring it
```

Every key is a field or a relation of the aggregate; every value is a `Criteria`, the same one
as outside. A scalar's is `{}`. A relation's carries `where`, `order`, `pagination` and its own
`specification`, so the vocabulary inside a node is the vocabulary outside it, recursively.

`None` and `{}` are not the same thing. `None` is «nothing asked»: every scalar, no relation.
`{}` is «asked, and nothing survived»: the identity, and the display field when the entity
names one (`Meta.display`). That pair is the reference a select lists and a many2one carries.

## The rules

| # | Rule |
|---|---|
| 1 | Without a specification: every scalar, including every key such as `dataset_id`, and no relation resolved. |
| 2 | Naming a node brings all of its scalars and none of its relations. `"plan": {}` is the whole plan. |
| 3 | Naming its children cuts it to those children. |
| 4 | A relation is never expanded without being named, at any level. |
| 5 | The identity always travels, at every level, asked or not. |
| 6 | What is asked is a union: order does not matter, repeats collapse, a bare node wins over its own children. |
| 7 | What cannot be answered is dropped and reported, never a 400: `unknown_field`, `not_expandable`. |
| 8 | An empty specification keeps the identity and, when the entity names one, its display field. |

## What the entity answers: its `DEFAULT_*` class methods

How an aggregate is read is said once, on the class, so a select, a list and a detail do not
each assemble it. `Entity` answers five class methods, each with a default, and a subclass
overrides the ones it needs. They are defaults: every part a caller's criteria names wins
([Entity reads](entity-reads.md)).

```python
from dataclasses import dataclass, field

from sincpro_framework.ddd import (
    TEXT, Any, Condition, Criteria, Entity, Operator, Sort, Specification,
)


@dataclass
class Partner(Entity):
    name: str
    city: str = ""


@dataclass
class Account(Entity):
    code: str
    name: str = ""
    partner_id: str | None = None
    partner: Partner | None = None
    lines: list["Line"] = field(default_factory=list)

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({
            "code": {}, "name": {},
            "partner": {"specification": {}},                    # identity and display
            "lines": {"pagination": {"limit": 300}},             # in Line's DEFAULT_ORDER
        })

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
        return Criteria(where=Any(any=[
            Condition(field="code", value=TEXT),
            Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT),
            Condition(field="name", operator=Operator.LIKE, value=TEXT),
        ]))
```

| Class method | What it answers | `Entity`'s default |
|---|---|---|
| `DEFAULT_GET_ID` | The field `Get` and `GetMany` find a record by | `"id"` |
| `DEFAULT_READING` | What one record brings, at any depth: a `Specification` | `None`: every scalar, no relation |
| `DEFAULT_ORDER` | The order of a list, and of this entity's records where another one holds them | `(Sort(field="id", descending=True),)`: newest first |
| `DEFAULT_DISPLAY` | The field shown beside the identity (rule 8) | the field called `name`, when there is one |
| `DEFAULT_LITERAL_SEARCH` | How a typed text finds records: a template whose `where` holds `TEXT` | containment on a text display; `None` without one |

`Partner` declares nothing and needs nothing: `name` is its display and a literal finds it by
containment.

- **Always `@classmethod`, always upper case.** Upper case so it never meets a field and a
  reader knows the framework calls it; a class method because that is what it overrides. A
  value (`DEFAULT_GET_ID = "code"`), a `@staticmethod` or a field with one of these names is
  refused when the class is declared.
- **Checked when the class is described.** A key, a display or an order over a field that does
  not exist; a key that is not a text, integer or uuid field; a reading that is not a
  `Specification` (a `where` there would hide records from every read); a template that never
  uses `TEXT`, sets anything besides `where`, or asks a prefix of a number — each is refused
  naming it. A mapped class whose key is not the identity needs that column unique.
- **`DEFAULT_READING` is a `Specification`, and its nodes are criteria.** The order, page and
  depth of a relation live in its node; a relation whose node names no order comes in the
  related entity's own `DEFAULT_ORDER`. Below the first level a name is not checked at import:
  a store drops it and reports it (rule 7).
- **The key is how a record is asked for, not its identity.** `DEFAULT_GET_ID = "code"` makes
  `Get(id="1.2.3")` read the account coded `1.2.3`; it is still stored, related and ordered by
  `id`.

### The literal search template

`TEXT` marks where the typed text goes; it may stand alone or inside a list
(`code in [TEXT, "0"]`). `matching` fills it:

| Condition | Operator | `"1.2.3"` finds |
|---|---|---|
| `Condition(field="code", value=TEXT)` | `=` | `1.2.3` |
| `Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT)` | `starts with` | `1.2.3`, `1.2.30`, `1.2.3.01` — not `1.2.1.2.3` |
| `Condition(field="name", operator=Operator.LIKE, value=TEXT)` | `like` | any text holding `1.2.3`, case-insensitive |

A literal is read as itself: `50%` finds `Descuento 50% off`, and `a_b` does not find `axb`.
A blank literal drops the conditions that hold `TEXT` and keeps the rest of the template: an
`active = true` beside the text still applies.

`describe` publishes all of it on `Meta` (`get_id`, `display`, `search`, `default_order`,
`detail`) — the template as a criteria, so a client fills it the same way — and two functions
turn it into the criteria a Feature runs:

| Call | What comes back |
|---|---|
| `accounts.search(matching(Account, "1.2.3"))` | a page of `NAME_SEARCH_LIMIT` (8), identity and display on the wire |
| `accounts.search(matching(Account, ""))` | the first 8 in the list order |
| `accounts.get(account_id)` | the stored `Account`: every scalar, no relation |
| `accounts.get(account_id, detail=detail_of(Account))` | that one `Account`, as `DEFAULT_READING` brings it |

`matching` and `detail_of` build a criteria and never run it. `EntityReads` runs them for a
project's own DTOs: [Entity reads](entity-reads.md). A list asks nothing extra: the
entity's order is `Meta.default_order`, which the store applies when a criteria names none.

### Form hints: defaults for a client, never rules

`presentation` says how a form shows each field — and only that. Every field of `Meta.fields`
carries the result, so a client builds a form without being told twice:

```python
from sincpro_framework.ddd import AllHold, Is, Presentation, When
from sincpro_framework.ddd.criteria import Operator


@dataclass
class Invoice(Entity):
    partner_id: str
    number: str = "/"
    state: InvoiceState = InvoiceState.DRAFT
    payment: Payment = Payment.CASH
    card_reference: str = ""
    amount: Decimal = Decimal("0")

    presentation = Presentation["Invoice"](
        readonly=lambda i: i.number,
        readonly_when=lambda i: When(
            Is(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount
        ),
        required_when=lambda i: When(
            Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference
        ),
        visible_when=lambda i: When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
    )
```

| `FieldMeta` | A form | Left alone |
|---|---|---|
| `readonly` | shows the field read-only | the fields the framework writes: `id`, `created_at`, `updated_at`, `version`, a mixin's |
| `required` | asks for it | a field with no default that cannot be `None` |
| `default` | starts a new record with it | the dataclass default, as JSON; nothing for a `default_factory` |
| `readonly_when` | shows it read-only while the condition holds over the record | — |
| `required_when` | asks for it while the condition holds | — |
| `visible_when` | shows it only while the condition holds | always shown |

A condition is the triple a criteria carries, written through the entity:
`Is(i.state, Operator.NE, InvoiceState.DRAFT)` is published as
`{"field": "state", "operator": "!=", "value": "draft"}`. `AllHold(...)` and `AnyHolds(...)`
combine them; a field named by two `When` is covered while either holds. A client evaluates it
with the same evaluator it filters with (`@sincpro/criteria`), reading each value as its
field's type: an amount travels as the text `"0"`.

**They are hints.** The framework checks none of them: a confirmed invoice saved with another
partner, or paid by card with no reference, is saved. A component that says otherwise wins —
a form may ask for a field the definition does not, or hide one by the user's groups. A rule
the server keeps is the project's own hook, and it can read the same condition so the rule is
written once:

```python
@billing_hooks.on(Invoice)
class PaymentNeedsItsReference(BillingHook):
    def before_save(self, invoice: Invoice) -> None:
        for name, condition in presentation_of(Invoice).required_when.items():
            if matches(invoice, condition) and not getattr(invoice, name):
                raise ConstraintViolation(f"{name} is required for this invoice")
```

Hiding a field in a form does not keep it from the answer: a field that must not reach a
caller is cut on the server, by the specification or by a hook (`after_read`).

Rule 1 said another way: with `dataset_id: str` and `dataset: Dataset` on an aggregate, a read
that asks nothing returns `dataset_id` and not `dataset`. `Meta.fields["dataset"]`, of type `many2one`, tells the
client the relation exists and what identifies it, so it can ask for it or filter by the key
without expanding anything.

**Two criterias merge with two laws.** `where` accumulates with AND, because both restrictions
hold. `specification` intersects, because a mask can only take away. That is what makes it safe
as a permission: cutting twice never shows more than cutting once, and two masks with nothing
in common leave the identity alone, never everything.

**No ceilings, only defaults.** A relation node without a `pagination` gets the default page,
40 per parent for a to-many. A client that writes `limit: 10000000` gets ten million. This is a
framework; the provider that wants a ceiling merges a criteria of its own on the way in.

## What the repository does with it

Today, the mask over scalars, end to end:

- `Meta.accept_specification` keeps what the model has, as a column or as a relation, and
  reports the rest in `dropped`. The repository does this beside the filter, so a page carries
  both kinds of dropped in the one list.
- `Meta.only(specification)` cuts the definition by the same mask, identity included. A client
  that asked for three fields sees three in the records and three in its filter builder.
- `ResponsePaginatedQuery.of` applies the mask where the answer is serialised and nowhere
  earlier: on the wire each record shows the named scalars plus the identity; inside the
  process the records stay the typed aggregates they are.

Resolution of a named relation. The vocabulary, `Relation`, `Resolver` and `BusResolver`, lives in
`sincpro_framework.ddd.relations` with no database in it; the kinds only a database has and the
SQL partitioning live in `orm/sqlalchemy/services/relation_resolver.py`:

- **One declaration, in the data mapper, beside the table.** `map_aggregates(..., relations=
  {Dataset: {"runs": Relation.foreign_key(Run, identified_by="dataset_id")}})`. The annotation on the
  aggregate gives the cardinality (`runs: list[Run]` to-many, `dataset: Dataset | None` to-one);
  the declaration gives the related aggregate, what identifies the relation and how to resolve it. `describe()` publishes all of it
  in `Meta.fields` as a field of type `many2one`, `one2many` or `many2many`, with `relation`,
  `identified_by` and, once expanded, `definition`: one map, the shape of Odoo's `fields_get`,
  and `Meta.relations` is only the view over the relational ones. An embedded value object in a
  JSON column is a field of type `embedded` whose `definition` is its shape; the specification
  cuts inside it the way it cuts a relation, without a page because it travels with the row.
  Foreign keys are read off the tables, so `many2one` and `one2many` need no declaration; only
  the table in between and what lives elsewhere are declared.
- **Four kinds, one shape.** `Relation.foreign_key` in the same database, with `Relation.many_to_many` with the table in between for a many-to-many; `Relation.id_list` for a list of ids held in a JSON column;
  `Relation.bus(Related, bus, Command, identified_by=…)` for another bounded context, whose command
  carries the reflected criteria and answers a paged response; `Relation.resolved_by(Related,
  identified_by=…, resolver=fn)` for HTTP, raw SQL, a serialised dictionary or anything, `fn(keys, criteria)`. The Feature that reads
  does not change between them.
- **Once per node, never per row.** Same-database kinds run one statement over the target with
  the parents' keys, partitioned by parent with `row_number() OVER (PARTITION BY key ORDER BY …)`
  so the node's `order` and per-parent `limit` hold in SQL, `count(*) OVER` giving each parent
  its exact count. Bus and function kinds make one call with the parents' keys and are cut per
  parent in memory. A nested specification recurses over all the children of a node at once.
- **What lands on the record.** A to-many becomes an `EntityCollection` on the parent's
  attribute, with `count` and a cursor: paging on inside a relation is an ordinary `search` on
  the target with the parent's key as filter and that cursor. A to-one becomes the record or
  `None`. The answer writes them under the node's name.
- **Lazy inside a unit of work, refused outside.** Inside `context()` a Feature navigates
  `dataset.producer.plan.owner` and `entry.lines` on first touch, whole, any depth, all three
  cardinalities, through the same resolvers. Outside a context a relation nobody named raises
  `RelationNotResolved`, because a loop over two hundred rows would be two hundred queries
  hidden in an attribute access. A record built in memory keeps what its constructor was given.
- **Defaults, not ceilings.** A to-many node without a page brings 40 per parent; a node that
  says a limit gets that limit.

## The request and the answer

```json
{"where": {"field": "name", "operator": "like", "value": "ventas"},
 "pagination": {"limit": 20},
 "specification": {
   "name": {},
   "producer": {"specification": {"stage": {}}},
   "sources": {"where": {"field": "row_count", "operator": ">", "value": 0},
               "order": [{"field": "registered_at", "descending": true}],
               "pagination": {"limit": 5},
               "specification": {"name": {}}}}}
```

Scalars come flat; a to-one as an object or `null`; a to-many as `{"items", "count", "cursor"}`.
Keys are the node names as asked, never dotted paths. The definition mirrors the mask, and for
every expanded relation carries the target's definition cut the same way, with what identifies it.
