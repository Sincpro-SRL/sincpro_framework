# Extending an entity you do not own

A module in another repository adds fields to an entity of the core — a delivery to a move, a
fiscal number to an invoice — **without editing the core**, and the commands the core already has
go on working. The rule behind it: *what an extension adds, the extension owns* — its table, its
migrations, its release. The reasons, the alternatives we rejected and the measurements are in the
[specification](../spec/extensibility.md); this page is how to do it.

Every block here runs, in order, in `tests/docs/test_persistence_guide.py`.

## The core, written once

The core knows nothing about extensions. It publishes a contract version on the entity, so that a
module can say what it was written against.

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import Consolidated, Database, Repository, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import Condition, Criteria, Entity

tables = MetaData()
mapper_registry = registry(metadata=tables)


@dataclass
class Move(Entity):
    CONTRACT: ClassVar[str] = "2.3"          # a major that breaks, a minor that adds

    number: str = ""
    total: Decimal = Decimal("0")


move_table = entity_table(
    "move", tables, Column("number", Text), Column("total", Numeric(12, 2), nullable=False)
)
map_aggregates(mapper_registry, {Move: move_table})
```

## A module adds a delivery

An extension is a subclass with a table of its own, whose `id` is the core record's. It says the
contract it needs and the fields of the core it reads; if the core no longer has them, the
application does not start.

```python
@dataclass
class MoveDelivery(Move):
    route: str = ""


delivery_table = Table(
    "move_delivery",
    tables,
    Column("id", Text, ForeignKey("move.id"), primary_key=True),
    Column("route", Text),
)
map_aggregates(
    mapper_registry,
    {MoveDelivery: delivery_table},
    requires={Move: "2.1"},
    uses={Move: ("number", "total")},
)

database = Database("sqlite://")
tables.create_all(database.engine)
store = Repository(database)
```

The extension's table is created by *its* migration, in its own chain; see the
[migrations guide](../migrations/README.md). An extension that stores its columns in the entity's own
table is mapped by giving the subclass that table (`{MoveZone: move_table}`); the migrations then
compare each chain only with the columns it declares.

## Reading answers each row as what it is

Nothing in the core's queries changes. Reading the core returns a record as the most derived
extension it has, with that extension's fields:

```python
store.save(Move(id="A", number="F-A", total=Decimal("1")))
store.save(MoveDelivery(id="B", number="F-B", total=Decimal("2"), route="R7"))

found = {one.number: one for one in store.search(Move, Criteria())}

assert type(found["F-A"]) is Move
assert type(found["F-B"]) is MoveDelivery and found["F-B"].route == "R7"
```

A `Get` and a `Search` written for `Move` before the module existed now answer `route` too, flat,
and the answer's schema describes it. A core that must not pay for that answers only itself:
`map_aggregates(…, strict=[Move])`. Writing the answer with each extension under its own key —
`{"number": …, "delivery": {"route": …}}` — is `delegated=[Move]`.

### The same answer, nested

A client that groups by module asks the core to write each extension under its own key. It is a
shape of the answer, chosen by the entity, and nothing about where the extension is stored:

```python
from sincpro_framework.ddd import ResponsePaginatedQuery


class ResponseMoves(ResponsePaginatedQuery):
    moves: list[Move]


def written(delegated: bool):
    if delegated:
        map_aggregates(mapper_registry, {}, delegated=[Move])
    page = store.search(Move, Criteria(where=Condition(field="id", value="B")))
    answer = ResponseMoves.of(page, Criteria()).model_dump(mode="json")["moves"][0]
    return {name: answer[name] for name in ("number", "route", "delivery") if name in answer}


assert written(delegated=False) == {"number": "F-B", "route": "R7"}
assert written(delegated=True) == {"number": "F-B", "delivery": {"route": "R7"}}
```

An extension the record does not have is `null`; the form reads the record in that shape and sends
it back the same way to the core's `Edit`, which takes the nested values (and the flat ones). The
rules, one by one with their tests, and where the code is, are in the
[specification](../spec/extensibility.md#delegated-answers-what-they-are-for).

## Filtering by what the extension adds

The core's criteria names a field of an extension like any other. A record without the extension
holds NULL there:

```python
found = store.search(Move, Criteria(where=Condition(field="route", value="R7")))

assert [one.number for one in found] == ["F-B"]
```

`ModelIntrospection.describe(Move).fields["route"].owner` says whose the field is. It is not a field to order by
(a record may lack it), and filtering a report over millions of rows by it reads a subquery: for
that, read a consolidated record.

## Several modules, one record

Two modules that extend the same entity are siblings, and no class can inherit both. A module that
knows them all declares the flat record over their tables:

```python
@dataclass
class MoveFiscal(Move):
    cufd: str = ""


fiscal_table = Table(
    "move_fiscal",
    tables,
    Column("id", Text, ForeignKey("move.id"), primary_key=True),
    Column("cufd", Text),
)
map_aggregates(mapper_registry, {MoveFiscal: fiscal_table})
fiscal_table.create(database.engine)


@dataclass
class MoveBoth(Entity):
    number: str = ""
    total: Decimal = Decimal("0")
    route: str = ""
    cufd: str = ""


map_aggregates(mapper_registry, {MoveBoth: Consolidated(Move, MoveDelivery, MoveFiscal)})
store.save(MoveBoth(id="C", number="F-C", route="R9", cufd="X1"))

both = store.get(MoveBoth, "C")

assert both is not None and (both.route, both.cufd) == ("R9", "X1")
```

It is read through an outer join and written to each table, the core's first. Editing it gives a
record the row of an extension it lacks; removing it removes the rows it has. A class that is only
read may leave columns out; one that is saved holds every `NOT NULL` column of the tables.

## What the module proves in its own CI

The module runs the contract against the core it will be installed on, before anyone upgrades:

```python
from sincpro_framework.runtime.testing.contracts.extension import ExtensionContract


class TestDeliveryExtension(ExtensionContract):
    entity = Move
    extension = MoveDelivery
    uses = ("number", "total")
    requires = "2.1"

    def make_store(self):
        return store
```

pytest collects the inherited tests: the fields it reads still exist, the contract is one it
accepts, the core's definition marks its fields, and a record of the extension is stored, read back
as itself and removed.

## Creating and editing through the core's commands

`Edit`, written once for the core, takes the fields of every extension installed. A new record whose
values carry `route` is created as a `MoveDelivery`; a stored `Move` edited with `route` is given the
delivery row first. Fields of two siblings that no class holds are refused, naming them. How, and
the tests that hold it, are in the [specification](../spec/extensibility.md#creating-and-editing-through-the-cores-commands).

## When there are hundreds of extensions

The type of a row is worked out from the extension rows that exist, which costs one probe per
extension. A core whose table has a nullable `extension_type` column stores it instead, and the cost
stops depending on how many are installed; `store.reclassify(Move)` types the rows that were there
before. The numbers are in the specification.
