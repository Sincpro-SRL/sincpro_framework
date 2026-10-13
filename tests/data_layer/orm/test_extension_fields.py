"""What the fields an extension adds are, once they are part of the entity: described, filtered,
written out, shaped as a client asks, created and edited — through the commands of the entity
that was written before any extension existed.

`docs/spec/extensibility.md` §5. Runs on SQLite and, with `SINCPRO_POSTGRES_URL`, on PostgreSQL.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text, text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.context.adapters.stores import InMemoryContexts
from sincpro_framework.data_layer.orm import Consolidated, Relation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import (
    Criteria,
    Entity,
    Get,
    ResponsePaginatedQuery,
    ResponseRecord,
    Search,
)
from sincpro_framework.ddd.criteria import (
    Condition,
    Grouping,
    Level,
    Measure,
    Sort,
    Specification,
)
from sincpro_framework.ddd.editing.edits import Edit, EditAction, EntityEdits, ResponseEdit
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.exceptions import ClientError, ProgrammingError

from .engines import fresh


class World:
    """A core `Move` and its extensions, as separate modules would install them."""

    def __init__(self, engine_url: str, delegated: bool = False, strict: bool = False):
        self.md = MetaData()
        self.reg = registry(metadata=self.md)

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

        @dataclass
        class MoveFiscal(Move):
            cufd: str = ""

        self.Move, self.MoveDelivery = Move, MoveDelivery
        self.MoveTracking, self.MoveFiscal = MoveTracking, MoveFiscal
        move_t = entity_table(
            "move",
            self.md,
            Column("number", Text),
            Column("total", Numeric(12, 2), nullable=False),
        )
        keyed = lambda name, *columns, parent="move": Table(  # noqa: E731
            name,
            self.md,
            Column("id", Text, ForeignKey(f"{parent}.id"), primary_key=True),
            *columns,
        )
        self.tables = {
            MoveDelivery: keyed("move_delivery", Column("route", Text)),
            MoveTracking: keyed(
                "move_tracking", Column("carrier", Text), parent="move_delivery"
            ),
            MoveFiscal: keyed("move_fiscal", Column("cufd", Text)),
        }
        map_aggregates(
            self.reg,
            {Move: move_t},
            delegated=[Move] if delegated else [],
            strict=[Move] if strict else [],
        )
        for extension, table in self.tables.items():
            map_aggregates(self.reg, {extension: table})
        self.database = fresh(engine_url, self.md)
        self.store = Repository(self.database)

    def fill(self):
        s, (Move, MoveDelivery, MoveTracking) = self.store, (
            self.Move,
            self.MoveDelivery,
            self.MoveTracking,
        )
        s.save(Move(id="A", number="F-A", total=Decimal("1")))
        s.save(MoveDelivery(id="B", number="F-B", total=Decimal("2"), route="R7"))
        s.save(
            MoveTracking(id="C", number="F-C", total=Decimal("3"), route="R7", carrier="DHL")
        )
        s.save(MoveDelivery(id="D", number="F-D", total=Decimal("4"), route="R9"))
        return self


def test_the_definition_of_the_core_says_whose_each_field_is(engine_url):
    world = World(engine_url)

    core = describe(world.Move).fields
    delivery = describe(world.MoveDelivery).fields

    assert core["number"].owner is None
    assert (core["route"].owner, core["carrier"].owner, core["cufd"].owner) == (
        "MoveDelivery",
        "MoveTracking",
        "MoveFiscal",
    )
    assert (
        core["route"].nullable and not core["route"].sortable
    )  # a record may have no delivery
    assert delivery["route"].owner is None and delivery["carrier"].owner == "MoveTracking"


def test_the_core_is_filtered_by_a_field_of_an_extension(engine_url):
    world = World(engine_url).fill()

    found = world.store.search(
        world.Move, Criteria(where=Condition(field="route", value="R7"))
    )

    assert sorted(one.number for one in found) == ["F-B", "F-C"] and not found.dropped
    assert [type(one).__name__ for one in found] == [
        "MoveTracking",
        "MoveDelivery",
    ] or sorted(type(one).__name__ for one in found) == ["MoveDelivery", "MoveTracking"]


def test_a_field_of_an_extension_is_not_a_field_to_order_by_because_a_record_may_lack_it(
    engine_url,
):
    world = World(engine_url).fill()

    with pytest.raises(InvalidCriteria, match="nullable"):
        world.store.search(world.Move, Criteria(order=(Sort(field="route"),)))


def test_the_core_is_filtered_ordered_and_grouped_with_a_field_of_an_extension_in_play(
    engine_url,
):
    world = World(engine_url).fill()

    ordered = world.store.search(
        world.Move,
        Criteria(
            where=Condition(field="route", value="R7"),
            order=(Sort(field="total", descending=True),),
        ),
    )
    grouped = world.store.group_by_levels(
        world.Move,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="route"),),
                measures={"total": Measure(function="sum", field="total")},
            )
        ),
    )

    assert [one.number for one in ordered] == ["F-C", "F-B"]
    found = {bucket.value: bucket.measures["total"] for bucket in grouped}
    assert found == {None: Decimal("1"), "R7": Decimal("5"), "R9": Decimal("4")}


# ── the shape of an answer ─────────────────────────────────────────────────────


class Responses:
    def __init__(self, world: World):
        Move = world.Move

        class ResponseMove(ResponseRecord):
            move: Move  # type: ignore[valid-type]

        class ResponseMoves(ResponsePaginatedQuery):
            moves: list[Move]  # type: ignore[valid-type]

        self.one, self.many = ResponseMove, ResponseMoves


def written(world: World, record_id: str, specification: Specification | None = None):
    responses = Responses(world)
    criteria = Criteria(specification=specification)
    page = world.store.search(
        world.Move,
        Criteria(where=Condition(field="id", value=record_id), specification=specification),
    )
    return responses.many.of(page, criteria).model_dump(mode="json")["moves"][0]


def test_an_answer_writes_the_fields_of_an_extension_flat_by_default(engine_url):
    world = World(engine_url).fill()

    record = written(world, "C")

    assert (record["route"], record["carrier"], record["number"]) == ("R7", "DHL", "F-C")
    assert "delivery" not in record


def test_an_answer_nests_each_extension_under_its_key_when_the_entity_delegates(engine_url):
    world = World(engine_url, delegated=True).fill()

    tracked, plain, delivered = written(world, "C"), written(world, "A"), written(world, "B")

    assert tracked["number"] == "F-C" and "route" not in tracked
    assert tracked["delivery"] == {"route": "R7"} and tracked["tracking"] == {
        "carrier": "DHL"
    }
    assert plain["delivery"] is None and plain["tracking"] is None  # the shape does not move
    assert delivered["delivery"] == {"route": "R7"} and delivered["tracking"] is None


def test_a_specification_that_names_a_field_of_an_extension_brings_it(engine_url):
    world = World(engine_url).fill()

    with_route = written(
        world, "B", Specification({"number": Criteria(), "route": Criteria()})
    )
    without = written(world, "B", Specification({"number": Criteria()}))

    assert with_route["route"] == "R7"
    assert "route" not in without


def test_the_schema_of_an_answer_describes_the_fields_of_the_extensions(engine_url):
    flat = Responses(World(engine_url))
    nested = Responses(World(engine_url, delegated=True))

    flat_schema = flat.many.model_json_schema(mode="serialization")
    nested_schema = nested.many.model_json_schema(mode="serialization")

    items = flat_schema["properties"]["moves"]["items"]
    assert {"route", "carrier", "cufd"} <= set(items["properties"])
    assert "route" not in items["properties"].get("route", {}).get("required", [])
    delegated = nested_schema["properties"]["moves"]["items"]["properties"]
    assert {"delivery", "tracking", "fiscal"} <= set(delegated) and "route" not in delegated


# ── the commands of the core, written before any extension ─────────────────────


def forms(world: World):
    Move = world.Move

    class ResponseEditMove(ResponseEdit):
        move: Move  # type: ignore[valid-type]

    class QueryGetMove(Get[Move, Responses(world).one]):  # type: ignore[misc, valid-type]
        pass

    class QuerySearchMoves(Search[Move, Responses(world).many]):  # type: ignore[misc, valid-type]
        pass

    class CommandEditMove(Edit[Move, ResponseEditMove]):  # type: ignore[valid-type]
        pass

    bus = UseFramework("accounting", log_after_execution=False)
    bus.add_dependency("repository", world.store)
    bus.context_store(InMemoryContexts())

    @bus.feature([QueryGetMove, QuerySearchMoves, CommandEditMove])
    class MoveForms(EntityEdits[Move]):  # type: ignore[valid-type]
        pass

    return bus, CommandEditMove, ResponseEditMove, QueryGetMove


def test_the_same_get_of_the_core_returns_the_extension_flat_with_nothing_declared(
    engine_url,
):
    world = World(engine_url).fill()
    bus, _, _, QueryGetMove = forms(world)

    body = bus(QueryGetMove(id="B"), Responses(world).one).model_dump(mode="json")["move"]

    assert (body["number"], body["route"]) == ("F-B", "R7")


def test_a_new_record_with_the_fields_of_an_extension_is_created_as_that_extension(
    engine_url,
):
    world = World(engine_url)
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        bus(
            Edit_(
                id="N",
                values={"number": "F-N", "route": "R1", "carrier": "UPS"},
                action=EditAction.SAVE,
            ),
            Response,
        )

    made = world.store.get(world.Move, "N")
    assert type(made).__name__ == "MoveTracking"
    assert (made.route, made.carrier) == ("R1", "UPS")  # type: ignore[union-attr]


def test_a_stored_record_without_the_extension_is_given_it_by_an_edit_that_names_its_fields(
    engine_url,
):
    world = World(engine_url).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        bus(Edit_(id="A", values={"route": "R5"}, action=EditAction.SAVE), Response)

    after = world.store.get(world.Move, "A")
    assert type(after).__name__ == "MoveDelivery" and after.route == "R5"  # type: ignore[union-attr]
    assert after is not None and after.number == "F-A"
    with world.database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM move")).scalar() == 4


def test_fields_of_sibling_extensions_that_no_class_holds_are_refused_naming_them(engine_url):
    world = World(engine_url)
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}), pytest.raises(
        ClientError, match="no one class holds"
    ):
        bus(
            Edit_(id="X", values={"route": "R1", "cufd": "C1"}, action=EditAction.SAVE),
            Response,
        )


def test_an_edit_of_a_draft_with_a_field_of_an_extension_is_kept_and_answered(engine_url):
    world = World(engine_url).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        answer = bus(Edit_(id="B", values={"route": "R3"}), Response)

    assert answer.move.route == "R3"  # type: ignore[attr-defined]
    assert world.store.get(world.MoveDelivery, "B").route == "R7"  # type: ignore[union-attr]  # nothing stored


# ── delegated: a form reads the record nested and sends it back as it read it ────


def nested_record(world: World, key: str) -> dict:
    """What a client holds after reading a record of a delegated entity: the extensions under
    their keys, `null` for the ones the record does not have."""
    return {
        name: value
        for name, value in written(world, key).items()
        if name not in ("id", "created_at", "updated_at", "version")
    }


def test_a_form_that_read_a_delegated_record_sends_it_back_as_it_read_it(engine_url):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)
    values = nested_record(world, "B")
    assert values["delivery"] == {"route": "R7"} and values["tracking"] is None
    values["number"], values["delivery"]["route"] = "F-B2", "R-NEW"

    with bus.context({"user_id": "u1"}):
        bus(Edit_(id="B", values=values, action=EditAction.SAVE), Response)

    stored = world.store.get(world.MoveDelivery, "B")
    assert stored is not None and (stored.number, stored.route) == ("F-B2", "R-NEW")


def test_an_extension_the_record_does_not_have_writes_nothing_when_the_form_sends_it_null(
    engine_url,
):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)
    values = nested_record(world, "A")
    assert values["delivery"] is None
    values["number"] = "F-A2"

    with bus.context({"user_id": "u1"}):
        bus(Edit_(id="A", values=values, action=EditAction.SAVE), Response)

    after = world.store.get(world.Move, "A")
    assert type(after).__name__ == "Move" and after.number == "F-A2"  # type: ignore[union-attr]


def test_a_delegated_form_creates_a_record_as_the_extension_its_nested_values_name(
    engine_url,
):
    world = World(engine_url, delegated=True)
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        bus(
            Edit_(
                id="N",
                values={
                    "number": "F-N",
                    "delivery": {"route": "R1"},
                    "tracking": {"carrier": "UPS"},
                },
                action=EditAction.SAVE,
            ),
            Response,
        )

    made = world.store.get(world.Move, "N")
    assert type(made).__name__ == "MoveTracking"
    assert (made.route, made.carrier) == ("R1", "UPS")  # type: ignore[union-attr]


def test_a_delegated_entity_takes_flat_values_as_they_are(engine_url):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        bus(Edit_(id="B", values={"route": "R-FLAT"}, action=EditAction.SAVE), Response)

    assert world.store.get(world.MoveDelivery, "B").route == "R-FLAT"  # type: ignore[union-attr]


def test_a_nested_value_with_a_field_the_extension_does_not_have_is_refused_naming_it(
    engine_url,
):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}), pytest.raises(
        ClientError, match="delivery has no field cufd"
    ):
        bus(
            Edit_(id="B", values={"delivery": {"cufd": "X"}}, action=EditAction.SAVE),
            Response,
        )


def test_an_entity_that_does_not_delegate_does_not_take_its_extensions_nested(engine_url):
    world = World(engine_url).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}), pytest.raises(
        ClientError, match="no field delivery"
    ):
        bus(
            Edit_(id="B", values={"delivery": {"route": "R1"}}, action=EditAction.SAVE),
            Response,
        )


def test_the_answer_to_an_edit_nests_the_extensions_when_the_entity_delegates(engine_url):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        answer = bus(Edit_(id="B", values={"delivery": {"route": "R3"}}), Response)

    shown = answer.model_dump(mode="json")["move"]
    assert shown["delivery"] == {"route": "R3"} and "route" not in shown


def test_the_fields_a_form_reports_changed_are_taken_nested_or_flat(engine_url):
    world = World(engine_url, delegated=True).fill()
    bus, Edit_, Response, _ = forms(world)

    with bus.context({"user_id": "u1"}):
        version = 0
        for changed in (["delivery"], ["delivery.route"], ["route"]):
            answer = bus(
                Edit_(
                    id="B",
                    values={"delivery": {"route": "R3"}},
                    changed=changed,
                    version=version,
                ),
                Response,
            )
            version = answer.draft.version  # type: ignore[union-attr]
            assert answer.move.route == "R3"  # type: ignore[attr-defined]


def test_two_extensions_that_would_nest_under_one_key_are_refused_naming_both(engine_url):
    world = World(engine_url, delegated=True)

    @dataclass
    class MoveDelivery(world.Move):  # another module's class, the same name
        zone: str = ""

    table = Table(
        "move_delivery_zone",
        world.md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("zone", Text),
    )

    with pytest.raises(ProgrammingError, match="would both be `delivery`"):
        map_aggregates(world.reg, {MoveDelivery: table})


def test_an_extension_that_is_not_an_entity_does_not_repeat_the_dates_and_version_of_the_base(
    engine_url,
):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""

    @dataclass
    class MoveFiscal:  # not an Entity: the id, dates and version are the move's
        id: str = ""
        cufd: str = ""

    move_t = entity_table("move", md, Column("number", Text))
    fiscal_t = Table(
        "move_fiscal",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("cufd", Text),
    )
    map_aggregates(
        reg,
        {Move: move_t, MoveFiscal: fiscal_t},
        relations={
            Move: {
                "fiscal": Relation.foreign_key(
                    MoveFiscal, parent_field="id", related_field="id", many=False
                )
            }
        },
    )
    store = Repository(fresh(engine_url, md))
    store.save(Move(id="M1", number="F-1"))
    store.save(MoveFiscal(id="M1", cufd="C1"))
    specification = Specification({"number": Criteria(), "fiscal": Criteria()})

    class ResponseMoves(ResponsePaginatedQuery):
        moves: list[Move]

    page = store.search(Move, Criteria(specification=specification))
    shown = ResponseMoves.of(page, Criteria(specification=specification)).model_dump(
        mode="json"
    )

    assert shown["moves"][0]["fiscal"] == {"id": "M1", "cufd": "C1"}


# ── removing and consolidating ─────────────────────────────────────────────────


def test_removing_a_plain_core_instance_takes_the_rows_of_its_extensions_with_it(engine_url):
    world = World(engine_url, strict=True).fill()  # a core that answers only itself
    plain = world.store.get(world.Move, "C")
    assert plain is not None and type(plain).__name__ == "Move"

    world.store.remove(plain)

    with world.database.engine.connect() as connection:
        counts = [
            connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ("move", "move_delivery", "move_tracking")
        ]
    assert counts == [3, 2, 0]


def test_a_consolidated_record_without_a_row_of_an_extension_is_edited_by_giving_it_the_row(
    engine_url,
):
    world = World(engine_url).fill()

    @dataclass
    class MoveAll(Entity):
        number: str = ""
        route: str = ""
        cufd: str = ""

    map_aggregates(
        world.reg, {MoveAll: Consolidated(world.Move, world.MoveDelivery, world.MoveFiscal)}
    )
    one = world.store.get(MoveAll, "A")
    assert one is not None
    one.cufd = "C9"

    world.store.save(one)

    with world.database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT cufd FROM move_fiscal WHERE id = 'A'")).scalar()
            == "C9"
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM move_delivery WHERE id = 'A'")
            ).scalar()
            == 0
        )
    assert world.store.get(MoveAll, "A").cufd == "C9"  # type: ignore[union-attr]


def test_removing_a_consolidated_record_without_all_its_rows_removes_the_ones_it_has(
    engine_url,
):
    world = World(engine_url).fill()

    @dataclass
    class MoveAll(Entity):
        number: str = ""
        route: str = ""
        cufd: str = ""

    map_aggregates(
        world.reg, {MoveAll: Consolidated(world.Move, world.MoveDelivery, world.MoveFiscal)}
    )

    world.store.remove(world.store.get(MoveAll, "B"))

    with world.database.engine.connect() as connection:
        counts = [
            connection.execute(text(f"SELECT COUNT(*) FROM {table} WHERE id = 'B'")).scalar()
            for table in ("move", "move_delivery", "move_fiscal")
        ]
    assert counts == [0, 0, 0]
