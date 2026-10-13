"""Extending an entity you do not own, and what happens when its owner releases a new version.

`docs/spec/extensibility.md` says what an extension may do and what a release of the core may not
break. Every claim there that is marked as working is held here, on SQLite and, when
`SINCPRO_POSTGRES_URL` is set, on PostgreSQL.

The cast: `Invoice` is the core's, a context of `sales`. A fiscal module and a delivery module
live in other repositories and add to it **without editing it**:

- a table of their own, keyed by the invoice's `id`, owned and migrated by them;
- an attribute on the core class that resolves it (`invoice.fiscal`), installed from outside;
- an expression read out of a JSON slot the core reserves (`invoice.zone`);
- or, when it is a concept of its own, a bounded context with another database, reached by the bus.

**A world per test, with classes of its own.** A relation is installed once per class and a second
call changes nothing, so two topologies cannot share one `Invoice`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    Numeric,
    Table,
    Text,
    event,
    text,
    update,
)
from sqlalchemy.orm import column_property, registry

from sincpro_framework import Feature, UseFramework
from sincpro_framework.data_layer.orm import Relation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Grouping,
    Level,
    Measure,
    Operator,
    Specification,
)
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery

from .engines import fresh

# ── the core, in the versions its owner releases ──────────────────────────────


def core(md: MetaData, version: int = 1) -> tuple[type, Any]:
    """The core's `Invoice` and its table, as released.

    v1  number, total                                    what the extensions were written against
    v2  + currency                                       a minor: a field is added
    v3  `total` is `amount`                              a major: a field is renamed
    """
    if version == 1:

        @dataclass
        class Invoice(Entity):  # pyright: ignore[reportRedeclaration]
            number: str = ""
            total: Decimal = Decimal("0")

        columns = [Column("number", Text), Column("total", Numeric(12, 2))]
    elif version == 2:

        @dataclass
        class Invoice(Entity):  # type: ignore[no-redef]  # pyright: ignore[reportRedeclaration]
            number: str = ""
            total: Decimal = Decimal("0")
            currency: str = "BOB"

        columns = [
            Column("number", Text),
            Column("total", Numeric(12, 2)),
            Column("currency", Text),
        ]
    else:

        @dataclass
        class Invoice(Entity):  # type: ignore[no-redef]  # pyright: ignore[reportRedeclaration]
            number: str = ""
            amount: Decimal = Decimal("0")

        columns = [Column("number", Text), Column("amount", Numeric(12, 2))]
    return Invoice, entity_table("invoice", md, *columns, Column("props", JSON))


# ── the modules of other repositories ─────────────────────────────────────────


def fiscal_module(reg: registry, invoice: type, invoice_t: Any) -> type:
    """What the fiscal repository ships: a class, a table of its own keyed by the invoice's id
    (with a real foreign key, since it shares the database), and the relation installed on the
    core class from outside."""

    @dataclass
    class InvoiceFiscal(Entity):
        cufd: str = ""
        control_code: str = ""

    table = entity_table(
        "invoice_fiscal",
        reg.metadata,
        Column("cufd", Text),
        Column("control_code", Text),
    )
    table.append_constraint(
        ForeignKeyConstraint(["id"], [invoice_t.c.id], ondelete="CASCADE")
    )
    # The framework reads a relation's cardinality from the class annotation, which an extension
    # cannot edit in the source: it declares the type of what it adds.
    invoice.__annotations__["fiscal"] = InvoiceFiscal | None
    map_aggregates(
        reg,
        {InvoiceFiscal: table},
        relations={
            invoice: {
                "fiscal": Relation.foreign_key(
                    InvoiceFiscal, parent_field="id", related_field="id"
                )
            }
        },
    )
    return InvoiceFiscal


def delivery_module(reg: registry, invoice: type, invoice_t: Any) -> type:
    """A second extension of the same entity: it shares nothing with the first one."""

    @dataclass
    class InvoiceDelivery(Entity):
        route: str = ""

    table = entity_table("invoice_delivery", reg.metadata, Column("route", Text))
    invoice.__annotations__["delivery_note"] = InvoiceDelivery | None
    map_aggregates(
        reg,
        {InvoiceDelivery: table},
        relations={
            invoice: {
                "delivery_note": Relation.foreign_key(
                    InvoiceDelivery, parent_field="id", related_field="id"
                )
            }
        },
    )
    return InvoiceDelivery


def zone_module(invoice_t: Any) -> dict[str, Any]:
    """An expression read out of the JSON slot the core reserves. `properties=` takes it."""
    return {"zone": column_property(invoice_t.c.props["zone"].as_string())}


# ── a world: tables created, rows in, the modules installed ───────────────────


class World:
    def __init__(self, engine_url: str, version: int = 1, create: bool = True) -> None:
        self.md = MetaData()
        self.reg = registry(metadata=self.md)
        self.invoice, self.invoice_t = core(self.md, version)
        self.url = engine_url
        self.create = create
        self.modules: list[str] = []
        self.extra: dict[str, Any] = {}
        self.fiscal: type | None = None
        self.delivery: type | None = None
        self.database: Database | None = None
        self.store: Repository | None = None

    def install(self, *names: str) -> "World":
        for name in names:
            self.modules.append(name)
            if name == "fiscal":
                self.fiscal = fiscal_module(self.reg, self.invoice, self.invoice_t)
            elif name == "delivery":
                self.delivery = delivery_module(self.reg, self.invoice, self.invoice_t)
            elif name == "zone":
                self.extra.update(zone_module(self.invoice_t))
        return self

    def open(self) -> "World":
        amount = "amount" if "amount" in self.invoice_t.c else "total"
        map_aggregates(
            self.reg,
            {self.invoice: self.invoice_t},
            properties={self.invoice: dict(self.extra)} if self.extra else None,
        )
        self.database = fresh(self.url, self.md) if self.create else Database(self.url)
        self.store = Repository(self.database)
        self.amount = amount
        return self

    def save_invoices(self, rows: list[tuple[str, int, dict | None, dict | None]]) -> None:
        """(number, total, fiscal columns, props) as the core and the modules write them."""
        assert self.store is not None
        with self.store.context() as unit:
            for number, total, fiscal, props in rows:
                record = self.invoice(number=number, **{self.amount: Decimal(total)})
                unit.save(record)
                if props is not None:
                    unit.session.execute(
                        update(self.invoice_t)
                        .where(self.invoice_t.c.id == record.id)
                        .values(props=props)
                    )
                if fiscal is not None and self.fiscal is not None:
                    other = self.fiscal(id=record.id, **fiscal)
                    unit.save(other)


@pytest.fixture
def make(engine_url: str):
    return lambda version=1, create=True: World(engine_url, version, create)


def statements(database: Database, run: Any) -> list[str]:
    """The SELECTs a piece of code runs."""
    seen: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(database.engine, "before_cursor_execute", count)
    try:
        run()
    finally:
        event.remove(database.engine, "before_cursor_execute", count)
    return seen


ASKING = Specification({"number": Criteria(), "fiscal": Criteria()})


# ── what an extension can do ──────────────────────────────────────────────────


def test_an_extension_adds_a_table_and_the_core_class_resolves_it(make):
    world = make().install("fiscal").open()
    world.save_invoices([("F-1", 100, {"cufd": "CUFD-A", "control_code": "X1"}, None)])
    assert world.store is not None

    page = world.store.search(world.invoice, Criteria(specification=ASKING))

    assert [one.fiscal.cufd for one in page] == ["CUFD-A"]  # type: ignore[attr-defined]


def test_the_extension_is_part_of_the_definition_the_client_reads(make):
    world = make().install("fiscal").open()

    fiscal = describe(world.invoice).fields["fiscal"]

    assert (fiscal.type.value, fiscal.relation, fiscal.many) == (
        "many2one",
        "InvoiceFiscal",
        False,
    )


def test_a_page_of_invoices_reads_its_extension_in_one_more_statement(make):
    world = make().install("fiscal").open()
    world.save_invoices(
        [(f"F-{n}", n, {"cufd": f"C{n}", "control_code": "x"}, None) for n in range(40)]
    )
    assert world.store is not None and world.database is not None

    seen = statements(
        world.database,
        lambda: list(world.store.search(world.invoice, Criteria(specification=ASKING))),  # type: ignore[union-attr]
    )

    assert (
        len(seen) <= 3
    )  # the page, its count and the extension for ALL of its rows — never 40


def test_two_extensions_of_one_entity_do_not_collide(make):
    world = make().install("fiscal", "delivery").open()
    assert world.store is not None and world.fiscal is not None and world.delivery is not None
    with world.store.context() as unit:
        record = world.invoice(number="F-1")
        unit.save(record)
        unit.save(world.fiscal(id=record.id, cufd="C1"))
        unit.save(world.delivery(id=record.id, route="R7"))

    asked = Specification({"fiscal": Criteria(), "delivery_note": Criteria()})
    [read] = list(world.store.search(world.invoice, Criteria(specification=asked)))

    assert (read.fiscal.cufd, read.delivery_note.route) == ("C1", "R7")  # type: ignore[attr-defined]


def test_a_field_read_out_of_the_json_slot_is_filtered_and_grouped_like_a_column(make):
    world = make().install("zone").open()
    world.save_invoices(
        [
            ("F-1", 10, None, {"zone": "N"}),
            ("F-2", 20, None, {"zone": "N"}),
            ("F-3", 5, None, {"zone": "S"}),
        ]
    )
    assert world.store is not None

    in_north = world.store.search(
        world.invoice, Criteria(where=Condition(field="zone", value="N"))
    )
    by_zone = world.store.group_by_levels(
        world.invoice,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="zone"),),
                measures={"total": Measure(function="sum", field=world.amount)},
            )
        ),
    )

    assert sorted(one.number for one in in_north) == ["F-1", "F-2"]
    assert {one.value: one.count for one in by_zone} == {"N": 2, "S": 1}
    assert describe(world.invoice).fields["zone"].groupable


# ── what happens when the core is released again ──────────────────────────────


def upgraded(make: Any, version: int, *modules: str) -> World:
    """The same database, opened by a newer release of the core with the same modules installed.

    The core's migration is run by hand — what a release's migration does — and nothing else is
    created, so what survives is what the modules own."""
    old = make(1).install("fiscal").open()
    old.save_invoices([("F-1", 100, {"cufd": "CUFD-A", "control_code": "X1"}, None)])
    assert old.database is not None
    with old.database.engine.begin() as connection:
        if version == 2:
            connection.execute(text("ALTER TABLE invoice ADD COLUMN currency TEXT"))
        elif version == 3:
            connection.execute(text("ALTER TABLE invoice RENAME COLUMN total TO amount"))
    new = make(version, False)
    new.url = old.url
    new.database = old.database
    new.install(*modules)
    return new


def test_a_minor_release_that_adds_a_field_leaves_the_extension_untouched(make):
    new = upgraded(make, 2, "fiscal")
    map_aggregates(new.reg, {new.invoice: new.invoice_t})
    new.store = Repository(new.database)  # type: ignore[arg-type]

    [read] = list(new.store.search(new.invoice, Criteria(specification=ASKING)))

    assert read.number == "F-1" and read.fiscal.cufd == "CUFD-A"  # type: ignore[attr-defined]
    assert "currency" in describe(new.invoice).fields


def test_a_major_release_that_renames_a_field_does_not_reach_a_table_that_names_only_the_id(
    make,
):
    new = upgraded(make, 3, "fiscal")
    map_aggregates(new.reg, {new.invoice: new.invoice_t})
    new.store = Repository(new.database)  # type: ignore[arg-type]

    [read] = list(new.store.search(new.invoice, Criteria(specification=ASKING)))

    assert read.amount == Decimal("100") and read.fiscal.cufd == "CUFD-A"  # type: ignore[attr-defined]


def test_an_extension_is_uninstalled_by_dropping_its_table_and_the_core_does_not_notice(make):
    world = make().install("fiscal").open()
    world.save_invoices([("F-1", 100, {"cufd": "CUFD-A"}, None)])
    assert world.database is not None
    with world.database.engine.begin() as connection:
        connection.execute(text("DROP TABLE invoice_fiscal"))

    bare = make(1, False)
    bare.url, bare.database = world.url, world.database
    map_aggregates(bare.reg, {bare.invoice: bare.invoice_t})
    bare.store = Repository(bare.database)  # type: ignore[arg-type]
    [read] = list(bare.store.search(bare.invoice, Criteria()))

    assert read.number == "F-1"


def test_a_core_row_that_is_deleted_takes_the_extension_row_with_it_when_they_share_a_database(
    make,
):
    world = make().install("fiscal").open()
    world.save_invoices([("F-1", 100, {"cufd": "CUFD-A"}, None)])
    assert world.database is not None and world.store is not None
    if world.database.engine.dialect.name == "sqlite":
        pytest.skip("SQLite enforces foreign keys only when asked to with a pragma")

    with world.database.engine.begin() as connection:
        connection.execute(text("DELETE FROM invoice"))
        left = connection.execute(text("SELECT count(*) FROM invoice_fiscal")).scalar()

    assert left == 0


# ── a concept of its own: another bounded context, another database ───────────


def distribution_world() -> tuple[Any, Any, list[Criteria], Any]:
    """`distribution` keeps deliveries in its own database and knows an invoice only by its id."""
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Delivery(Entity):
        invoice_id: str = ""
        route: str = ""

    table = entity_table(
        "delivery", md, Column("invoice_id", Text, nullable=False), Column("route", Text)
    )
    map_aggregates(reg, {Delivery: table})
    database = fresh("sqlite://", md)
    repository = Repository(database)
    calls: list[Criteria] = []

    class CommandSearchDeliveries(Query):
        pass

    class ResponseSearchDeliveries(ResponsePaginatedQuery):
        deliveries: list[Delivery]  # type: ignore[valid-type]

    bus = UseFramework("distribution", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandSearchDeliveries)
    class SearchDeliveries(Feature):
        repository: Repository

        def execute(self, dto: CommandSearchDeliveries) -> ResponseSearchDeliveries:
            calls.append(dto.criteria)
            return ResponseSearchDeliveries.of(
                self.repository.search(Delivery, dto.criteria), dto.criteria
            )

    return Delivery, bus, calls, (repository, CommandSearchDeliveries)


def test_data_that_lives_in_another_context_is_resolved_by_its_bus_one_call_per_page(make):
    Delivery, bus, calls, (distribution, Command) = distribution_world()
    world = make().open()
    assert world.store is not None
    world.invoice.__annotations__["delivery"] = Delivery | None
    map_aggregates(
        world.reg,
        {},
        relations={
            world.invoice: {
                "delivery": Relation.bus(
                    Delivery, bus, Command, parent_field="id", related_field="invoice_id"
                )
            }
        },
    )
    with world.store.context() as unit:
        records = [world.invoice(number=f"F-{n}") for n in range(30)]
        for one in records:
            unit.save(one)
    with distribution.context() as unit:
        for index, one in enumerate(records):
            unit.save(Delivery(invoice_id=one.id, route=f"R{index % 3}"))

    page = world.store.search(
        world.invoice,
        Criteria(specification=Specification({"number": Criteria(), "delivery": Criteria()})),
    )
    routes = sorted({one.delivery.route for one in page})  # type: ignore[attr-defined]

    assert routes == ["R0", "R1", "R2"]
    assert len(calls) == 1  # thirty invoices, one question to the other context
    assert len(calls[0].where.value) == 30  # type: ignore[union-attr]


def test_invoices_are_filtered_by_a_field_the_other_context_owns_by_asking_it_for_the_ids(
    make,
):
    Delivery, bus, calls, (distribution, _) = distribution_world()
    world = make().open()
    assert world.store is not None
    with world.store.context() as unit:
        records = [world.invoice(number=f"F-{n}") for n in range(30)]
        for one in records:
            unit.save(one)
    with distribution.context() as unit:
        for index, one in enumerate(records):
            unit.save(Delivery(invoice_id=one.id, route="R7" if index % 10 == 0 else "R1"))

    ids = [
        one.invoice_id
        for one in distribution.search(
            Delivery, Criteria(where=Condition(field="route", value="R7"))
        )
    ]
    found = world.store.search(
        world.invoice, Criteria(where=Condition(field="id", operator=Operator.IN, value=ids))
    )

    assert sorted(one.number for one in found) == ["F-0", "F-10", "F-20"]


def test_the_module_the_specification_shows_runs_exactly_as_it_is_written(make):
    """The code of «A module today» is taken from the page and executed, so the page cannot say
    something the framework does not do."""
    import re
    from pathlib import Path

    page = (Path(__file__).parents[3] / "docs" / "spec" / "extensibility.md").read_text()
    section = page.split("## 10. A module today", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S)
    assert code is not None
    shown: dict[str, Any] = {}
    exec(code.group(1), shown)  # noqa: S102  (our own documentation)

    world = make()
    shown["install"](world.reg, world.invoice, world.invoice_t)
    world.open()
    assert world.store is not None
    with world.store.context() as unit:
        record = world.invoice(number="F-1")
        unit.save(record)
        unit.save(shown["InvoiceFiscal"](id=record.id, cufd="CUFD-DOC", control_code="X"))

    [read] = list(
        world.store.search(
            world.invoice, Criteria(specification=Specification({"fiscal": Criteria()}))
        )
    )

    assert read.fiscal.cufd == "CUFD-DOC"  # type: ignore[attr-defined]


# ── using the entity's attributes from the extension ──────────────────────────


def test_an_extension_reaches_every_attribute_of_the_entity_through_a_back_reference(make):
    """`delivery.move.total`: one hop back to the core record, typed as the core's class."""
    world = make().install("fiscal").open()
    assert world.store is not None and world.fiscal is not None
    world.fiscal.__annotations__["invoice"] = world.invoice | None
    map_aggregates(
        world.reg,
        {},
        relations={
            world.fiscal: {
                "invoice": Relation.foreign_key(
                    world.invoice, parent_field="id", related_field="id"
                )
            }
        },
    )
    with world.store.context() as unit:
        record = world.invoice(number="F-1", **{world.amount: Decimal("300")})
        unit.save(record)
        unit.save(world.fiscal(id=record.id, cufd="C1"))

    asked = Criteria(specification=Specification({"cufd": Criteria(), "invoice": Criteria()}))
    [read] = list(world.store.search(world.fiscal, asked))

    assert (read.cufd, read.invoice.number, read.invoice.total) == (  # type: ignore[attr-defined]
        "C1",
        "F-1",
        Decimal("300"),
    )


def subclass_world(engine_url: str):
    """The extension as a SUBCLASS of the entity: a table of its own whose `id` is the parent's."""
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class AccountMove(Entity):
        number: str = ""
        total: Decimal = Decimal("0")

    @dataclass
    class DeliveryMove(AccountMove):
        route: str = ""

    @dataclass
    class FiscalMove(AccountMove):
        cufd: str = ""

    move_t = entity_table(
        "account_move", md, Column("number", Text), Column("total", Numeric(12, 2))
    )
    delivery_t = Table(
        "delivery_move",
        md,
        Column("id", Text, ForeignKey("account_move.id"), primary_key=True),
        Column("route", Text),
    )
    fiscal_t = Table(
        "fiscal_move",
        md,
        Column("id", Text, ForeignKey("account_move.id"), primary_key=True),
        Column("cufd", Text),
    )
    map_aggregates(reg, {AccountMove: move_t, DeliveryMove: delivery_t, FiscalMove: fiscal_t})
    database = fresh(engine_url, md)
    return AccountMove, DeliveryMove, FiscalMove, database, Repository(database)


def test_an_extension_declared_as_a_subclass_has_every_attribute_of_the_entity_flat(
    engine_url,
):
    AccountMove, DeliveryMove, _, _, store = subclass_world(engine_url)
    move = DeliveryMove(number="F-2", total=Decimal("200"), route="R7")
    store.save(move)

    read = store.get(DeliveryMove, move.id)

    assert read is not None
    assert (read.number, read.total, read.route) == ("F-2", Decimal("200"), "R7")


def test_a_record_the_core_created_becomes_the_extended_kind_once_its_row_exists(engine_url):
    AccountMove, DeliveryMove, _, database, store = subclass_world(engine_url)
    plain = AccountMove(number="F-1", total=Decimal("100"))
    store.save(plain)
    before = store.get(DeliveryMove, plain.id)
    with database.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO delivery_move (id, route) VALUES (:i, 'R9')"), {"i": plain.id}
        )

    after = store.get(DeliveryMove, plain.id)

    assert before is None  # no extension row: not (yet) a delivery move
    assert after is not None and (after.number, after.route) == ("F-1", "R9")
    # reading the core answers downstream: the row is now the extended kind, whoever asks
    assert [type(one).__name__ for one in store.search(AccountMove, Criteria())] == [
        "DeliveryMove"
    ]


def test_two_subclass_extensions_of_one_entity_are_two_kinds_and_never_one_object(engine_url):
    AccountMove, DeliveryMove, FiscalMove, database, store = subclass_world(engine_url)
    move = DeliveryMove(number="F-1", total=Decimal("1"), route="R1")
    store.save(move)
    with database.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO fiscal_move (id, cufd) VALUES (:i, 'C1')"), {"i": move.id}
        )

    as_delivery, as_fiscal = store.get(DeliveryMove, move.id), store.get(FiscalMove, move.id)

    assert as_delivery is not None and as_delivery.route == "R1"
    assert as_fiscal is not None and as_fiscal.cufd == "C1"
    assert not hasattr(as_delivery, "cufd") and not hasattr(as_fiscal, "route")
