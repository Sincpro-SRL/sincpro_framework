# A complete example: one base, six modules

One accounting base, and six modules from other repositories that add to its `Move` without editing
it, each in a different mode. Read it top to bottom: every block runs, in order, in
`tests/docs/test_persistence_guide.py`, and the `assert`s are what the framework answers.
[The guide](extensions.md) explains each piece; [the specification](../spec/extensibility.md) says
why.

| Module | Adds | Mode |
|---|---|---|
| accounting (the base) | `number`, `total`, a JSON slot, a stored type of its rows | the core |
| distribution | `route`, then `carrier` | a **chain**: a table of its own per level |
| zones | `zone` | **the core's own table** |
| labels | `priority` | a key of the **JSON slot** |
| fiscal | `cufd` | a **separate entity**, reached by an attribute |
| invoicing | `cufd` | a **sibling** of distribution, a table of its own |

## 1. The base, written once

It knows nothing of the modules. It publishes its contract version, reserves the JSON slot, and has
a column to store the type of its rows (so reading it costs the same with ten modules or two
hundred).

```python
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.context.adapters.stores import InMemoryContexts
from sincpro_framework.data_layer.orm import (
    Consolidated,
    Database,
    Relation,
    Repository,
    map_aggregates,
    template_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import (
    Condition,
    Criteria,
    Entity,
    Get,
    ResponsePaginatedQuery,
    ResponseRecord,
    Search,
)
from sincpro_framework.ddd.criteria import Specification
from sincpro_framework.ddd.editing.edits import Edit, EditAction, EntityEdits, ResponseEdit

tables = MetaData()
mapper_registry = registry(metadata=tables)


@dataclass
class Move(Entity):
    CONTRACT: ClassVar[str] = "2.3"  # a major that breaks, a minor that adds
    number: str = ""
    total: Decimal = Decimal("0")
    properties: dict[str, Any] = field(default_factory=dict)  # the JSON slot


move_table = entity_table(
    "move",
    tables,
    template_table.properties_column(),
    Column("number", Text),
    Column("total", Numeric(12, 2), nullable=False),
    Column("extension_type", Text),  # the core stores the type of its rows
)
map_aggregates(mapper_registry, {Move: move_table})
```

## 2. Distribution: a chain, a table per level

Each level is a subclass whose table's `id` is the parent's. It says the contract it needs and the
fields of the core it reads; if the core no longer has them, the application does not start.

```python
@dataclass
class MoveDelivery(Move):
    route: str = ""


@dataclass
class MoveTracking(MoveDelivery):
    carrier: str = ""


delivery_table = Table(
    "move_delivery",
    tables,
    Column("id", Text, ForeignKey("move.id"), primary_key=True),
    Column("route", Text),
)
tracking_table = Table(
    "move_tracking",
    tables,
    Column("id", Text, ForeignKey("move_delivery.id"), primary_key=True),
    Column("carrier", Text),
)
map_aggregates(
    mapper_registry,
    {MoveDelivery: delivery_table},
    requires={Move: "2.1"},
    uses={Move: ("number", "total")},
)
map_aggregates(mapper_registry, {MoveTracking: tracking_table})
```

## 3. Zones: columns in the core's own table

The module's migration adds the column to a table that is not its own; the migrations compare each
chain only with the columns it declares, so neither drops the other's.

```python
@dataclass
class MoveZone(Move):
    zone: str | None = None


move_table.append_column(Column("zone", Text))  # what the module's migration adds
map_aggregates(mapper_registry, {MoveZone: move_table})
```

## 4. Labels: a key of the JSON slot as a field

No table and no migration. The values live in the `properties` dictionary; the module maps a key of
it as a field, to filter, group and measure by.

```python
map_aggregates(
    mapper_registry,
    {},
    properties={
        Move: {
            "priority": template_table.slot_field(
                move_table, "priority", template_table.SlotType.INTEGER
            )
        }
    },
)
```

## 5. Fiscal: a separate entity, reached by an attribute

Not a subclass: its own table, and `move.fiscal` holds it. It is a plain dataclass and **not an
`Entity`**, so it does not repeat the move's dates and version.

```python
@dataclass
class MoveFiscal:
    id: str = ""
    cufd: str = ""


fiscal_table = Table(
    "move_fiscal",
    tables,
    Column("id", Text, ForeignKey("move.id", ondelete="CASCADE"), primary_key=True),
    Column("cufd", Text),
)
map_aggregates(
    mapper_registry,
    {MoveFiscal: fiscal_table},
    relations={
        Move: {
            "fiscal": Relation.foreign_key(
                MoveFiscal, parent_field="id", related_field="id", many=False
            )
        }
    },
)
```

## 6. Invoicing: a sibling of distribution

Another module extending the same `Move`, with a table of its own. Two siblings cannot be one
subclass, which section 11 solves.

```python
@dataclass
class MoveInvoice(Move):
    cufd: str = ""


invoice_table = Table(
    "move_invoice",
    tables,
    Column("id", Text, ForeignKey("move.id"), primary_key=True),
    Column("cufd", Text),
)
map_aggregates(mapper_registry, {MoveInvoice: invoice_table})
```

## 7. The data

The tables are created by each module's migration; here, all at once. Every record is saved with its
own class, and one `save` writes every level, the core's first.

```python
database = Database("sqlite://")
tables.create_all(database.engine)
store = Repository(database)

store.save(Move(id="A", number="F-A", total=Decimal("1"), properties={"priority": 3}))
store.save(MoveDelivery(id="B", number="F-B", total=Decimal("2"), route="R7"))
store.save(MoveTracking(id="C", number="F-C", total=Decimal("3"), route="R7", carrier="DHL"))
store.save(MoveZone(id="D", number="F-D", total=Decimal("4"), zone="Z1"))
store.save(MoveFiscal(id="A", cufd="CUFD-A"))
```

## 8. Reading the base answers each row as what it is

```python
types = {one.id: type(one).__name__ for one in store.search(Move, Criteria())}

assert types == {
    "A": "Move",
    "B": "MoveDelivery",
    "C": "MoveTracking",
    "D": "MoveZone",
}
```

On the wire it is flat, and the answer's schema describes the fields the modules added:

```python
class ResponseMoves(ResponsePaginatedQuery):
    moves: list[Move]


def shown(criteria: Criteria | None = None) -> dict[str, dict]:
    criteria = criteria or Criteria()
    answer = ResponseMoves.of(store.search(Move, criteria), criteria)
    return {
        one["id"]: {
            name: value
            for name, value in one.items()
            if name not in ("created_at", "updated_at", "version", "properties")
        }
        for one in answer.model_dump(mode="json")["moves"]
    }


flat = shown()
assert flat["A"] == {"id": "A", "number": "F-A", "total": "1.00"}
assert flat["C"] == {
    "id": "C", "number": "F-C", "total": "3.00", "route": "R7", "carrier": "DHL",
}
assert flat["D"]["zone"] == "Z1"

schema = ResponseMoves.model_json_schema(mode="serialization")
assert {"route", "carrier", "zone", "cufd"} <= set(
    schema["properties"]["moves"]["items"]["properties"]
)
```

## 9. Filtering the base by fields it never had

```python
def ids(field_name: str, value: object) -> list[str]:
    found = store.search(Move, Criteria(where=Condition(field=field_name, value=value)))
    assert not found.dropped
    return sorted(one.id for one in found)


assert ids("route", "R7") == ["B", "C"]      # a table of its own
assert ids("zone", "Z1") == ["D"]            # the core's own table
assert ids("priority", 3) == ["A"]           # a key of the JSON slot
```

A record without the extension holds NULL there. Ordering by such a field is refused (it may be
absent), and `describe` says whose each field is:

```python
owners = {name: one.owner for name, one in describe(Move).fields.items()}

assert owners["number"] is None
assert (owners["route"], owners["carrier"], owners["zone"]) == (
    "MoveDelivery", "MoveTracking", "MoveZone",
)
```

## 10. The separate entity comes when the specification names it

```python
named = Specification({"number": Criteria(), "fiscal": Criteria()})

assert shown(Criteria(specification=named))["A"] == {
    "id": "A", "number": "F-A", "fiscal": {"id": "A", "cufd": "CUFD-A"},
}
```

## 11. The base's own commands, written before any module

A `Get`, a `Search` and an `Edit` for `Move`, with nothing about the modules. They now see all of it.

```python
class ResponseMove(ResponseRecord):
    move: Move


class ResponseEditMove(ResponseEdit):
    move: Move


class QueryGetMove(Get[Move, ResponseMove]):
    pass


class QuerySearchMoves(Search[Move, ResponseMoves]):
    pass


class CommandEditMove(Edit[Move, ResponseEditMove]):
    pass


bus = UseFramework("accounting", log_after_execution=False)
bus.add_dependency("repository", store)
bus.context_store(InMemoryContexts())


@bus.feature([QueryGetMove, QuerySearchMoves, CommandEditMove])
class MoveForms(EntityEdits[Move]):
    pass


got = bus(QueryGetMove(id="C"), ResponseMove).model_dump(mode="json")["move"]
assert (got["route"], got["carrier"]) == ("R7", "DHL")

with bus.context({"user_id": "u1"}):
    # a new record whose values carry fields of a module is created as that module's class
    bus(
        CommandEditMove(
            id="N",
            values={"number": "F-N", "route": "R1", "carrier": "UPS"},
            action=EditAction.SAVE,
        ),
        ResponseEditMove,
    )
    assert type(store.get(Move, "N")).__name__ == "MoveTracking"

    # a stored record is given the module's rows when an edit names its fields
    bus(CommandEditMove(id="A", values={"route": "R5"}, action=EditAction.SAVE), ResponseEditMove)
    assert type(store.get(Move, "A")).__name__ == "MoveDelivery"

    # a column the module keeps in the core's own table
    bus(CommandEditMove(id="D", values={"zone": "Z9"}, action=EditAction.SAVE), ResponseEditMove)
    assert store.get(MoveZone, "D").zone == "Z9"  # type: ignore[union-attr]
```

Fields of two siblings that no class holds are refused, naming them:

```python
from sincpro_framework.exceptions import ClientError

with bus.context({"user_id": "u1"}):
    try:
        bus(
            CommandEditMove(
                id="X", values={"route": "R", "cufd": "C"}, action=EditAction.SAVE
            ),
            ResponseEditMove,
        )
    except ClientError as refused:
        assert "no one class holds" in str(refused)
    else:
        raise AssertionError("siblings were taken as one class")
```

## 12. Siblings as one flat record

A module that knows both declares the record over their tables. It is a class of its own, so the
siblings can be one object; it is read through an outer join and written to each table.

```python
@dataclass
class MoveAll(Entity):
    number: str = ""
    total: Decimal = Decimal("0")
    route: str = ""
    cufd: str = ""


map_aggregates(mapper_registry, {MoveAll: Consolidated(Move, MoveDelivery, MoveInvoice)})
store.save(MoveAll(id="Z", number="F-Z", route="R2", cufd="CF"))

whole = store.get(MoveAll, "Z")

assert whole is not None and (whole.route, whole.cufd) == ("R2", "CF")
assert type(store.get(Move, "Z")).__name__ == "MoveDelivery"  # the base answers one of them
```

## 13. The same answer, grouped by module

The base can ask for each module under its own key instead of flat. It is the shape of the answer
only; where a module keeps its columns does not change. An extension a record does not have is
`null`, so the shape does not move with the row.

```python
map_aggregates(mapper_registry, {}, delegated=[Move])

grouped = shown()

assert grouped["C"] == {
    "id": "C", "number": "F-C", "total": "3.00",
    "delivery": {"route": "R7"}, "tracking": {"carrier": "DHL"},
    "zone": None, "invoice": None,
}
assert grouped["D"]["zone"] == {"zone": "Z9"} and grouped["D"]["delivery"] is None
```

A form reads a record in that shape and sends it back the same way to `Edit`, which takes the nested
values (and the flat ones) — see the
[specification](../spec/extensibility.md#delegated-answers-what-they-are-for).

## 14. What each module proves in its own CI

```python
from sincpro_framework.runtime.testing.contracts.extension import ExtensionContract


class TestDistribution(ExtensionContract):
    entity = Move
    extension = MoveDelivery
    uses = ("number", "total")
    requires = "2.1"

    def make_store(self):
        return store
```

pytest collects the inherited tests: the fields it reads still exist, the contract is one it accepts,
the core's definition marks the module's fields, and a record of the extension is stored, read back
as itself and removed.

## What each mode costs and gives

| Mode | Declare | The base reads it | Filter the base by it | A migration |
|---|---|---|---|---|
| Chain, a table per level | a subclass and its table | as the class, flat | a subquery | creates its table |
| The core's own table | a subclass given the core's table | as the class, flat | a column | adds columns to a table not its own |
| JSON slot | `slot_field` of a key | in `properties` | a subquery over the slot | none |
| Separate entity | a plain dataclass and a `Relation` | when the specification names it | no: filter its own class | creates its table |
| Sibling | a subclass, like a chain | the first one installed answers | a subquery | creates its table |
| Consolidated | `Consolidated(Core, A, B)` | one flat record over a join | a join | none |
