"""One aggregate whose relations are brought three different ways, each declaring its own
scope — so one page shows what a declared scope means on every mechanism there is:

    Invoice.lines       same database, a real foreign key      resolved in SQL
    Invoice.owner       a plain function, could be HTTP        resolved by the provider
    Invoice.events      another context, over its bus          resolved by a command

`InvoiceEvent` is a `DomainEvent` and not a business aggregate, and nothing changes because of
that: a relation never asks what it is resolving, only how (`ddd/entity/relations.py`).

**The contrast between `lines` and `events` is the point.** Lines belong to the invoice and sit
in its database, so the key is a real `ForeignKey` the engine enforces. Events belong to another
context and may sit in another database entirely, so no key is possible and the bus is the only
join — `two_databases` runs exactly that, with the same declaration `same_database` uses.

**A world per topology, with classes of its own.** `_install` registers a relation once per
class and a second call changes nothing (`orm/sqlalchemy/data_mapper.py`), so two topologies
cannot share one `Invoice`: the second declaration would be dropped in silence and both would
resolve against the first bus.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import Feature, UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    Relation,
    entity_table,
    event_columns,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

OWN_KIND = Criteria(where=Condition(field="entity_type", value="Invoice"))
"""The events scope: this log is addressed by `entity_type`/`entity_id`, and an id space is
shared, so without this a `Draft`'s event filed under an invoice's id would come along."""

NOT_VOID = Criteria(where=Condition(field="void", value=False))
"""The lines scope: a voided line stays in the table and out of the invoice."""

TRUSTED = Criteria(where=Condition(field="trusted", value=True))
"""The owner scope: what the provider is asked for, whether or not it honours it."""

ACTIVE = Criteria(where=Condition(field="active", value=True))
"""The customer scope, on a TO-ONE: a closed account resolves to `None` rather than to a
record this invoice may no longer show."""


@dataclass
class Owner:
    """Not mapped anywhere: what a function resolver answers with."""

    owner_id: str
    name: str
    trusted: bool = True


OWNERS = {
    "own_1": Owner(owner_id="own_1", name="Ada"),
    "own_2": Owner(owner_id="own_2", name="Grace"),
}


def _classes() -> tuple[type, type, type, type, type, type, type]:
    """The aggregate, its line, its event, the customer another context owns, and a collection
    for each of the paged ones — fresh, so one world's mapping never reaches another's."""

    @dataclass
    class InvoiceLine(Entity):
        invoice_id: str = ""
        description: str = ""
        amount: int = 0
        void: bool = False
        tax_code: str = ""

    @dataclass(kw_only=True)
    class InvoiceEvent(DomainEvent):
        name = "invoice.v1.event"
        said: str = ""

    @dataclass
    class Customer(Entity):
        """The other context's aggregate: pointed at by an id this one holds, and nothing
        else — the to-one half of what a bus resolves."""

        name: str = ""
        active: bool = True

    @dataclass
    class Invoice(Entity):
        number: str = ""
        state: str = "draft"
        owner_id: str = ""
        customer_id: str = ""
        tax_code: str = ""
        lines: list[InvoiceLine] = field(default_factory=list)
        owner: Owner | None = None
        customer: Customer | None = None
        events: list[InvoiceEvent] = field(default_factory=list)
        # The two relations matched on a pair where NEITHER side is an identity.
        taxed_lines: list[InvoiceLine] = field(default_factory=list)
        state_events: list[InvoiceEvent] = field(default_factory=list)

    class Invoices(EntityCollection[Invoice]):
        pass

    class InvoiceEvents(EntityCollection[InvoiceEvent]):
        pass

    class Customers(EntityCollection[Customer]):
        pass

    return Invoice, InvoiceLine, InvoiceEvent, Customer, Invoices, InvoiceEvents, Customers


def _tables(invoicing: registry, elsewhere: registry) -> tuple[Any, Any, Any, Any]:
    """Where the two mechanisms part company: `invoice_line` names `invoice.id` as a real
    foreign key, `invoice_event` names nothing — it cannot, and would not even if it could.
    `customer` sits with the events, on whatever engine the other side turns out to use."""
    invoice_table = entity_table(
        "invoice",
        invoicing.metadata,
        Column("number", Text),
        Column("state", Text, nullable=False),
        Column("owner_id", Text),
        Column("customer_id", Text),
        Column("tax_code", Text, nullable=False),
    )
    line_table = entity_table(
        "invoice_line",
        invoicing.metadata,
        Column("invoice_id", Text, ForeignKey("invoice.id"), nullable=False),
        Column("description", Text, nullable=False),
        Column("amount", Integer, nullable=False),
        Column("void", Boolean, nullable=False),
        Column("tax_code", Text, nullable=False),
    )
    event_table = entity_table(
        "invoice_event",
        elsewhere.metadata,
        *event_columns(),  # label, entity_type, entity_id, correlation_id, causation_id, sequence
        # Not nullable so a relation can declare an order by it: the translator refuses to page
        # by a nullable column, because that silently drops the rows holding no value.
        Column("said", Text, nullable=False),
    )
    customer_table = entity_table(
        "customer",
        elsewhere.metadata,
        Column("name", Text, nullable=False),
        Column("active", Boolean, nullable=False),
    )
    return invoice_table, line_table, event_table, customer_table


def _owners_resolver(seen: list[tuple[list, Criteria]]):
    """A provider that could be HTTP, raw SQL or a dictionary. It records what it was asked
    so a test can read the criteria that reached it — **honouring that criteria is the
    resolver's own job**, which is exactly why a test must be able to see it arrive."""

    def fetch(keys, criteria: Criteria) -> list[Owner]:
        seen.append((list(keys), criteria))
        return [OWNERS[key] for key in keys if key in OWNERS]

    return fetch


def _events_bus(
    repository: Repository, event: type, collection: type, calls: list[Criteria]
) -> tuple:
    """The context that owns the event log, with the `Query`-shaped command `Relation.bus`
    needs: `BusResolver` builds it as `command(criteria=…)` and nothing else."""

    class CommandSearchInvoiceEvents(Query):
        pass

    class ResponseSearchInvoiceEvents(ResponsePaginatedQuery):
        entries: list[event]  # type: ignore[valid-type]

    bus = UseFramework("invoice_events", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandSearchInvoiceEvents)
    class SearchInvoiceEvents(Feature):
        repository: Repository

        def execute(self, dto: CommandSearchInvoiceEvents) -> ResponseSearchInvoiceEvents:
            calls.append(dto.criteria)
            return ResponseSearchInvoiceEvents.of(
                self.repository.search(collection, dto.criteria), dto.criteria
            )

    return bus, CommandSearchInvoiceEvents


def _customers_bus(
    repository: Repository, customer: type, collection: type, calls: list[Criteria]
) -> tuple:
    """The other context again, this time answering a TO-ONE: the invoice holds the id and asks
    who it belongs to. Same `Relation.bus`, same `Query` command — only the cardinality of the
    annotation differs, and that is what flips which side the key is read from."""

    class CommandSearchCustomers(Query):
        pass

    class ResponseSearchCustomers(ResponsePaginatedQuery):
        customers: list[customer]  # type: ignore[valid-type]

    bus = UseFramework("customers", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandSearchCustomers)
    class SearchCustomers(Feature):
        repository: Repository

        def execute(self, dto: CommandSearchCustomers) -> ResponseSearchCustomers:
            calls.append(dto.criteria)
            return ResponseSearchCustomers.of(
                self.repository.search(collection, dto.criteria), dto.criteria
            )

    return bus, CommandSearchCustomers


def _populate(world: "World") -> dict[str, Any]:
    """Two invoices, and for each mechanism one record the scope must keep out.

    lines      F-1 has two, one of them voided
    events     F-1 has two, plus a `Draft`'s event filed under F-1's own id
    owner      F-1's is trusted, F-2's is not
    customer   F-1's is active, F-2's is not — the to-one the other context owns
    """
    active = world.customer(name="Ada", active=True)
    closed = world.customer(name="Grace", active=False)
    with world.elsewhere.context() as unit:
        unit.save(active)
        unit.save(closed)

    # Both invoices sit in state «paid» and share the tax code «VAT», so a relation matched on
    # those fields has to reach across the two — which is the whole point of the pair.
    first = world.invoice(
        number="F-1", state="paid", tax_code="VAT", owner_id="own_1", customer_id=active.id
    )
    second = world.invoice(
        number="F-2", state="paid", tax_code="VAT", owner_id="own_2", customer_id=closed.id
    )
    with world.invoices.context() as unit:
        unit.save(first)
        unit.save(second)
        unit.save(
            world.invoice_line(
                invoice_id=first.id,
                description="mug",
                amount=50,
                void=False,
                tax_code="VAT",
            )
        )
        unit.save(
            world.invoice_line(
                invoice_id=first.id,
                description="typo",
                amount=99,
                void=True,
                tax_code="VAT",
            )
        )
        unit.save(
            world.invoice_line(
                invoice_id=second.id,
                description="desk",
                amount=300,
                void=False,
                tax_code="EXEMPT",
            )
        )
    with world.elsewhere.context() as unit:
        unit.session.add_all(
            [
                world.invoice_event(entity_type="Invoice", entity_id=first.id, said="raised"),
                world.invoice_event(entity_type="Invoice", entity_id=first.id, said="paid"),
                world.invoice_event(
                    entity_type="Invoice", entity_id=second.id, said="raised"
                ),
                world.invoice_event(entity_type="Draft", entity_id=first.id, said="drafted"),
            ]
        )
    return {"F-1": first, "F-2": second, "customer": active, "closed": closed}


@dataclass
class World:
    """Everything one topology is made of, so a test can name what it needs."""

    invoices: Repository
    elsewhere: Repository
    invoice: type
    invoice_line: type
    invoice_event: type
    customer: type
    invoices_collection: type
    event_table: Any
    line_table: Any
    calls: list[Criteria]
    asked_of_customers: list[Criteria]
    asked_of_owners: list[tuple[list, Criteria]]
    records: dict[str, Any] = field(default_factory=dict)


def a_world(
    invoices_db: Database,
    elsewhere_db: Database,
    lines_scope: Criteria | None = NOT_VOID,
    owner_scope: Criteria | None = TRUSTED,
    events_scope: Criteria | None = OWN_KIND,
    customer_scope: Criteria | None = ACTIVE,
) -> World:
    """One aggregate, four relations, three mechanisms — against whichever engines are handed
    in. The declaration is identical in both topologies; only the engines differ.

    Each scope is a parameter so a test can take one away and watch what the scope was keeping
    out, or put a different one in.
    """
    (
        Invoice,
        InvoiceLine,
        InvoiceEvent,
        Customer,
        Invoices,
        InvoiceEvents,
        Customers,
    ) = _classes()
    invoicing, elsewhere = registry(), registry()
    invoice_table, line_table, event_table, customer_table = _tables(invoicing, elsewhere)

    map_aggregates(elsewhere, {InvoiceEvent: event_table, Customer: customer_table})
    invoicing.metadata.create_all(invoices_db.engine)
    elsewhere.metadata.create_all(elsewhere_db.engine)

    invoices_repository = Repository(invoices_db)
    elsewhere_repository = Repository(elsewhere_db)
    calls: list[Criteria] = []
    asked_of_customers: list[Criteria] = []
    asked_of_owners: list[tuple[list, Criteria]] = []
    events_bus, events_command = _events_bus(
        elsewhere_repository, InvoiceEvent, InvoiceEvents, calls
    )
    customers_bus, customers_command = _customers_bus(
        elsewhere_repository, Customer, Customers, asked_of_customers
    )

    map_aggregates(
        invoicing,
        {Invoice: invoice_table, InvoiceLine: line_table},
        relations={
            Invoice: {
                # SQL, same database, real foreign key. Declared rather than left to be
                # inferred from the column, because only a declaration can carry a scope.
                "lines": Relation.foreign_key(
                    InvoiceLine, identified_by="invoice_id", scope=lines_scope
                ),
                # A plain function: the criteria reaches it, honouring it is its business.
                "owner": Relation.resolved_by(
                    Owner,
                    identified_by="owner_id",
                    resolver=_owners_resolver(asked_of_owners),
                    scope=owner_scope,
                ),
                # Another context, over its bus — the only mechanism left when the records
                # live in a database this one cannot reach. TO-MANY: the key is a field of
                # the event, and what travels is this invoice's own id.
                "events": Relation.bus(
                    InvoiceEvent,
                    events_bus,
                    events_command,
                    identified_by="entity_id",
                    scope=events_scope,
                ),
                # The same bus kind, TO-ONE: now `identified_by` is a field of the INVOICE,
                # and what travels is the customer id this invoice holds. Same declaration
                # shape; the annotation's cardinality is what flips the side.
                "customer": Relation.bus(
                    Customer,
                    customers_bus,
                    customers_command,
                    identified_by="customer_id",
                    scope=customer_scope,
                ),
                # NEITHER side is an identity: the lines that carry this invoice's tax code,
                # whichever invoice they belong to. Impossible to declare with `identified_by`,
                # which can only ever anchor one of the two sides to an id.
                "taxed_lines": Relation.foreign_key(
                    InvoiceLine, parent_field="tax_code", related_field="tax_code"
                ),
                # The same idea across a bus: what was said that matches the state this
                # invoice is in, no matter whose event it was.
                "state_events": Relation.bus(
                    InvoiceEvent,
                    events_bus,
                    events_command,
                    parent_field="state",
                    related_field="said",
                ),
            }
        },
    )

    world = World(
        invoices=invoices_repository,
        elsewhere=elsewhere_repository,
        invoice=Invoice,
        invoice_line=InvoiceLine,
        invoice_event=InvoiceEvent,
        customer=Customer,
        invoices_collection=Invoices,
        event_table=event_table,
        line_table=line_table,
        calls=calls,
        asked_of_customers=asked_of_customers,
        asked_of_owners=asked_of_owners,
    )
    world.records = _populate(world)
    return world


def same_database(**scopes: Criteria | None) -> World:
    """Topology 1: one engine holds the invoice, its lines and its events. `lines` could be
    joined in SQL and is; `events` could be too, and deliberately is not."""
    database = Database("sqlite://")
    return a_world(database, database, **scopes)


def two_databases(tmp_path, **scopes: Criteria | None) -> World:
    """Topology 2: the events move to a database of their own. Not one line of the
    declaration changes — and now no foreign key could have reached them anyway."""
    return a_world(
        Database(f"sqlite:///{tmp_path}/invoices.sqlite3"),
        Database(f"sqlite:///{tmp_path}/invoice_events.sqlite3"),
        **scopes,
    )
