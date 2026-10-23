"""What a row is, once other modules have extended its entity: the extension type.

`docs/spec/extensibility.md` §4 says it. Reading an entity answers *downstream*: each row comes back
as the most derived extension it has, with that extension's fields, and nobody stores which. These
tests hold it on SQLite and, with `SINCPRO_POSTGRES_URL`, on PostgreSQL.

    Move ← MoveDelivery ← MoveTracking        a chain: each one a table whose id is the parent's
    Move ← MoveZone                           an extension whose column lives in Move's own table
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text, text
from sqlalchemy.orm import registry

from sincpro_framework import exceptions
from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import Condition, Criteria

from .engines import fresh


def chain_world(engine_url: str, strict: bool = False, stored: bool = False):
    """Three levels, as three modules would declare them: the core, then each extension."""
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""
        total: Decimal = Decimal("0")

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    @dataclass
    class MoveTracking(MoveDelivery):
        carrier: str = ""

    move_t = entity_table(
        "move",
        md,
        Column("number", Text),
        Column("total", Numeric(12, 2)),
        *([Column("extension_type", Text)] if stored else []),
    )
    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    tracking_t = Table(
        "move_tracking",
        md,
        Column("id", Text, ForeignKey("move_delivery.id"), primary_key=True),
        Column("carrier", Text),
    )
    # the core maps itself first; each extension is installed later, by its own call
    map_aggregates(reg, {Move: move_t}, strict=[Move] if strict else [])
    database = fresh(engine_url, md)
    return Move, MoveDelivery, MoveTracking, (reg, delivery_t, tracking_t), database


def install_chain(world):
    Move, MoveDelivery, MoveTracking, (reg, delivery_t, tracking_t), database = world
    map_aggregates(reg, {MoveDelivery: delivery_t})
    map_aggregates(reg, {MoveTracking: tracking_t})
    return Repository(database)


def rows(database, *statements):
    with database.engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def test_reading_the_core_answers_each_row_as_the_most_derived_extension_it_has(engine_url):
    world = chain_world(engine_url)
    Move, MoveDelivery, MoveTracking, _, database = world
    store = install_chain(world)
    for one in (
        Move(id="A", number="F-A"),
        MoveDelivery(id="B", number="F-B", route="R7"),
        MoveTracking(id="C", number="F-C", route="R8", carrier="DHL"),
    ):
        store.save(one)

    found = {one.number: one for one in store.search(Move, Criteria())}

    assert {number: type(one).__name__ for number, one in found.items()} == {
        "F-A": "Move",
        "F-B": "MoveDelivery",
        "F-C": "MoveTracking",
    }
    assert (found["F-B"].route, found["F-C"].route, found["F-C"].carrier) == (  # type: ignore[attr-defined]
        "R7",
        "R8",
        "DHL",
    )
    assert type(store.get(Move, "C")).__name__ == "MoveTracking"


def test_an_extension_installed_after_the_core_was_mapped_is_part_of_the_answer(engine_url):
    world = chain_world(engine_url)
    Move, MoveDelivery, _, (reg, delivery_t, _), database = world
    store = Repository(database)
    rows(
        database,
        "INSERT INTO move (id, created_at, version, number) VALUES ('A', CURRENT_TIMESTAMP, 1, 'F-A')",
    )
    before = type(store.get(Move, "A")).__name__

    map_aggregates(reg, {MoveDelivery: delivery_t})
    rows(database, "INSERT INTO move_delivery (id, route) VALUES ('A', 'R1')")
    after = store.get(Move, "A")

    assert before == "Move"
    assert type(after).__name__ == "MoveDelivery" and after.route == "R1"  # type: ignore[union-attr]


def test_reading_an_extension_answers_the_levels_below_it(engine_url):
    world = chain_world(engine_url)
    Move, MoveDelivery, MoveTracking, _, _ = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    store.save(MoveDelivery(id="B", number="F-B", route="R7"))
    store.save(MoveTracking(id="C", number="F-C", route="R8", carrier="DHL"))

    deliveries = {
        one.number: type(one).__name__ for one in store.search(MoveDelivery, Criteria())
    }
    trackings = [one.number for one in store.search(MoveTracking, Criteria())]

    assert deliveries == {"F-B": "MoveDelivery", "F-C": "MoveTracking"}
    assert trackings == ["F-C"]


def test_one_save_of_the_deepest_class_writes_every_level_in_one_transaction(engine_url):
    world = chain_world(engine_url)
    Move, _, MoveTracking, _, database = world
    store = install_chain(world)

    store.save(
        MoveTracking(id="C", number="F-C", total=Decimal("5"), route="R8", carrier="DHL")
    )

    with database.engine.connect() as connection:
        counts = [
            connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ("move", "move_delivery", "move_tracking")
        ]
    assert counts == [1, 1, 1]


def test_a_core_that_answers_only_itself_never_returns_an_extension(engine_url):
    world = chain_world(engine_url, strict=True)
    Move, MoveDelivery, _, _, _ = world
    store = install_chain(world)
    store.save(MoveDelivery(id="B", number="F-B", route="R7"))

    as_core = store.get(Move, "B")
    as_extension = store.get(MoveDelivery, "B")

    assert as_core is not None and type(as_core).__name__ == "Move"
    assert as_extension is not None and as_extension.route == "R7"


def test_the_core_filters_by_its_own_fields_whatever_the_extensions_are(engine_url):
    world = chain_world(engine_url)
    Move, MoveDelivery, _, _, _ = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A", total=Decimal("1")))
    store.save(MoveDelivery(id="B", number="F-B", total=Decimal("9"), route="R7"))

    found = store.search(Move, Criteria(where=Condition(field="total", value=Decimal("9"))))

    assert [(type(one).__name__, one.number) for one in found] == [("MoveDelivery", "F-B")]


# ── an extension whose columns live in the core's own table ───────────────────


def same_table_world(engine_url: str):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""

    @dataclass
    class MoveZone(Move):
        zone: str | None = None
        priority: int | None = None

    move_t = entity_table("move", md, Column("number", Text))
    map_aggregates(reg, {Move: move_t})
    # what the extension's own migration adds to a table that is not its own
    move_t.append_column(Column("zone", Text))
    move_t.append_column(Column("priority", Numeric(3, 0)))
    map_aggregates(reg, {MoveZone: move_t})
    return Move, MoveZone, fresh(engine_url, md)


def test_an_extension_in_the_core_table_is_read_as_the_extension_when_a_column_it_owns_is_set(
    engine_url,
):
    Move, MoveZone, database = same_table_world(engine_url)
    store = Repository(database)
    store.save(Move(id="A", number="F-A"))
    store.save(MoveZone(id="B", number="F-B", zone="Z1"))

    found = {one.number: one for one in store.search(Move, Criteria())}

    assert type(found["F-A"]).__name__ == "Move"
    assert type(found["F-B"]).__name__ == "MoveZone" and found["F-B"].zone == "Z1"  # type: ignore[attr-defined]
    assert [one.number for one in store.search(MoveZone, Criteria())] == ["F-B"]
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM move")).scalar() == 2
        )  # one table


def test_a_column_the_extension_owns_is_filtered_like_any_other(engine_url):
    Move, MoveZone, database = same_table_world(engine_url)
    store = Repository(database)
    store.save(MoveZone(id="B", number="F-B", zone="Z1"))
    store.save(MoveZone(id="C", number="F-C", zone="Z2"))

    found = store.search(MoveZone, Criteria(where=Condition(field="zone", value="Z2")))

    assert [one.number for one in found] == ["F-C"]


def test_an_extension_in_the_core_table_needs_a_column_of_its_own(engine_url):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""

    @dataclass
    class MoveEmpty(Move):
        zone: str = ""

    move_t = entity_table("move", md, Column("number", Text))
    map_aggregates(reg, {Move: move_t})

    with pytest.raises(
        exceptions.ProgrammingError, match="none of its own fields is a column"
    ):
        map_aggregates(reg, {MoveEmpty: move_t})


def test_an_extension_in_the_core_table_cannot_live_under_a_core_that_answers_only_itself(
    engine_url,
):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""

    @dataclass
    class MoveZone(Move):
        zone: str | None = None

    move_t = entity_table("move", md, Column("number", Text), Column("zone", Text))
    map_aggregates(reg, {Move: move_t}, strict=[Move])

    with pytest.raises(exceptions.ProgrammingError, match="answers only itself"):
        map_aggregates(reg, {MoveZone: move_t})


def test_a_core_release_that_adds_a_field_an_extension_has_is_refused_naming_both_tables(
    engine_url,
):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):  # the core, a minor release later: it now has `route`
        number: str = ""
        route: str = ""

    @dataclass
    class MoveDelivery(Move):  # the module, written when the core had no `route`
        route: str = ""

    move_t = entity_table("move", md, Column("number", Text), Column("route", Text))
    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    map_aggregates(reg, {Move: move_t})

    with pytest.raises(
        exceptions.ProgrammingError, match=r"route \(in move and move_delivery\)"
    ):
        map_aggregates(reg, {MoveDelivery: delivery_t})


# ── a core that stores the type of its rows ───────────────────────────────────


def statement_text(store, entity) -> str:
    return str(
        store.statement(entity, Criteria()).compile(compile_kwargs={"literal_binds": False})
    )


def test_a_core_that_stores_the_type_reads_it_without_asking_the_extension_tables(engine_url):
    world = chain_world(engine_url, stored=True)
    Move, MoveDelivery, MoveTracking, _, _ = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    store.save(MoveDelivery(id="B", number="F-B", route="R7"))
    store.save(MoveTracking(id="C", number="F-C", route="R8", carrier="DHL"))

    found = {one.number: type(one).__name__ for one in store.search(Move, Criteria())}

    assert found == {"F-A": "Move", "F-B": "MoveDelivery", "F-C": "MoveTracking"}
    assert "EXISTS" not in statement_text(store, Move)
    assert store.get(MoveTracking, "C").carrier == "DHL"  # type: ignore[union-attr]


def test_reading_an_extension_of_a_core_that_stores_the_type_answers_its_levels_below(
    engine_url,
):
    world = chain_world(engine_url, stored=True)
    Move, MoveDelivery, MoveTracking, _, _ = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    store.save(MoveDelivery(id="B", number="F-B", route="R7"))
    store.save(MoveTracking(id="C", number="F-C", route="R8", carrier="DHL"))

    found = {one.number: type(one).__name__ for one in store.search(MoveDelivery, Criteria())}

    assert found == {"F-B": "MoveDelivery", "F-C": "MoveTracking"}


def test_a_stored_record_given_an_extension_is_typed_as_it(engine_url):
    world = chain_world(engine_url, stored=True)
    Move, MoveDelivery, _, _, database = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    plain = store.get(Move, "A")

    store.extend(plain, MoveDelivery)

    assert type(store.get(Move, "A")).__name__ == "MoveDelivery"


def test_rows_an_extension_wrote_outside_the_framework_are_typed_by_reclassifying(engine_url):
    world = chain_world(engine_url, stored=True)
    Move, MoveDelivery, _, _, database = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    store.save(Move(id="B", number="F-B"))
    rows(database, "INSERT INTO move_delivery (id, route) VALUES ('B', 'R1')")
    assert (
        type(store.get(Move, "B")).__name__ == "Move"
    )  # the table says so, and nobody told it

    store.reclassify(Move)

    assert [type(store.get(Move, key)).__name__ for key in ("A", "B")] == [
        "Move",
        "MoveDelivery",
    ]


def test_a_stored_type_of_a_class_no_longer_installed_reads_as_the_core(engine_url):
    world = chain_world(engine_url, stored=True)
    Move, _, _, _, database = world
    store = install_chain(world)
    store.save(Move(id="A", number="F-A"))
    rows(database, "UPDATE move SET extension_type = 'gone.Module.Class' WHERE id = 'A'")

    assert type(store.get(Move, "A")).__name__ == "Move"


def test_a_core_that_stores_no_type_refuses_to_reclassify(engine_url):
    world = chain_world(engine_url)
    Move, _, _, _, _ = world
    store = install_chain(world)

    with pytest.raises(exceptions.ProgrammingError, match="stores no type"):
        store.reclassify(Move)


def test_the_core_is_filtered_by_a_column_an_extension_keeps_in_the_cores_own_table(
    engine_url,
):
    Move, MoveZone, database = same_table_world(engine_url)
    store = Repository(database)
    store.save(Move(id="A", number="F-A"))
    store.save(MoveZone(id="B", number="F-B", zone="Z1"))
    store.save(MoveZone(id="C", number="F-C", zone="Z2"))

    found = store.search(Move, Criteria(where=Condition(field="zone", value="Z2")))

    assert [one.number for one in found] == ["F-C"] and not found.dropped
