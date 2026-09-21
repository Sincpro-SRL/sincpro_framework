"""One aggregate, three mechanisms, one rule — what a relation can actually do.

    Invoice.lines       SQL, a real foreign key, same database
    Invoice.owner       a plain function resolver, could be HTTP
    Invoice.events      a command on another context's bus, possibly another database

Every one of them declares a `scope`, and the tests here are about proving the same three
things on all three: the scope narrows what comes back, a caller may narrow it further but
never widen past it, and a scope the other side cannot answer is refused rather than dropped.

The events relation carries the second question too — an aggregate's own event log, addressed
by `entity_type`/`entity_id` with no foreign key anywhere, resolved identically whether the log
shares an engine with the invoice or lives in a database of its own.
"""

import pytest

from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Operator,
    Specification,
    parse_order,
)
from sincpro_framework.ddd.exceptions import ContractViolation, RelationNotResolved
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

from .event_relation_models import World, same_database, two_databases

EVERYTHING = Criteria(
    specification=Specification(
        {
            "lines": Criteria(),
            "owner": Criteria(),
            "events": Criteria(),
            "customer": Criteria(),
        }
    )
)
"""The four relations asked for at once — the page every «one call» test reads."""


@pytest.fixture(scope="module")
def one_engine() -> World:
    return same_database()


@pytest.fixture(scope="module")
def two_engines(tmp_path_factory) -> World:
    return two_databases(tmp_path_factory.mktemp("relation_scope"))


@pytest.fixture(scope="module")
def unscoped() -> World:
    """The same three relations with no scope at all — what the scopes were holding back."""
    return same_database(lines_scope=None, owner_scope=None, events_scope=None)


@pytest.fixture(params=["one_engine", "two_engines"])
def world(request: pytest.FixtureRequest) -> World:
    """Whatever is about the relation and not about the topology runs in both."""
    return request.getfixturevalue(request.param)


def invoice_of(world: World, number: str, asked: Criteria = EVERYTHING):
    """One invoice with its relations resolved, read the way a use case would."""
    page = world.invoices.search(
        world.invoices_collection,
        asked.model_copy(
            update={"where": Condition(field="id", value=world.records[number].id)}
        ),
    )
    return page.first()


def test_the_lines_are_joined_by_a_real_foreign_key_and_the_events_by_nothing(world: World):
    """The contrast the whole file rests on. Lines belong to this context, so the engine
    enforces the key. Events belong to another, so there is no key to enforce — and the
    declaration does not change when they turn out to share an engine after all."""
    assert world.line_table.foreign_keys
    assert not world.event_table.foreign_keys


def test_one_call_resolves_sql_a_function_and_a_bus_alike(world: World):
    """What a relation is for: the caller asks once, and three different machines answer —
    a join, a function, and a command to another context — without the caller knowing which
    is which. Each honours the scope its mapper declared."""
    invoice = invoice_of(world, "F-1")

    assert [line.description for line in invoice.lines] == ["mug"]  # the voided one is out
    assert invoice.owner is not None and invoice.owner.name == "Ada"
    assert sorted(event.said for event in invoice.events) == ["paid", "raised"]


def test_without_a_scope_each_mechanism_brings_what_the_scope_keeps_out(unscoped: World):
    """Stated as the failure, because that is what makes the scope worth its line: a voided
    line is still a row of that invoice, and a `Draft`'s event filed under this invoice's id
    is indistinguishable from its own when only the id is matched."""
    invoice = invoice_of(unscoped, "F-1")

    assert sorted(line.description for line in invoice.lines) == ["mug", "typo"]
    assert sorted(event.said for event in invoice.events) == ["drafted", "paid", "raised"]


def test_a_sql_relation_pushes_its_scope_into_the_statement(world: World):
    """The scope is not a filter applied afterwards in Python: it is merged before the kind
    runs, so a foreign-key relation resolves it in the same statement as the join."""
    with world.invoices.context() as unit:
        invoice = unit.get(world.invoice, world.records["F-1"].id)
        assert invoice is not None
        assert [line.description for line in invoice.lines] == ["mug"]


def test_a_bus_relation_carries_its_scope_inside_the_command(world: World):
    """Nothing else could work: the other context is behind a bus and may be behind a network,
    so the only way a filter reaches it is inside the criteria the command carries."""
    world.calls.clear()

    invoice = invoice_of(world, "F-1")

    assert sorted(event.said for event in invoice.events) == ["paid", "raised"]
    asked = str(world.calls[-1].expression)
    assert "entity_type" in asked and "entity_id" in asked


def test_a_function_resolver_is_handed_the_scope_and_may_do_as_it_likes(world: World):
    """The honest boundary of a custom resolver: the framework merges the scope and hands it
    over, and whether the provider honours it is the provider's business — which is exactly
    why a test has to be able to see it arrive."""
    world.asked_of_owners.clear()

    invoice_of(world, "F-1")

    keys, criteria = world.asked_of_owners[-1]
    assert keys == ["own_1"]
    assert "trusted" in str(criteria.expression)


def test_a_to_one_over_a_bus_reads_its_key_from_the_parent(world: World):
    """The other half of `_keys_for`, and the one shape no test covered until now. On a
    to-many the key is a field of the related record and the parent's id travels; on a
    to-one it is the other way round — `identified_by="customer_id"` is a field of the
    INVOICE, and what goes to the other context is the id this invoice holds."""
    world.asked_of_customers.clear()

    invoice = invoice_of(world, "F-1")

    assert invoice.customer is not None and invoice.customer.name == "Ada"
    asked = str(world.asked_of_customers[-1].expression)
    assert world.records["customer"].id in asked  # the id the invoice held, not the invoice's


def test_a_to_one_over_a_bus_honours_its_scope_by_answering_none(world: World):
    """F-2's customer is closed, and the relation declares `active == True`. A to-one whose
    record the scope excludes is `None` — not the excluded record, and not an error."""
    invoice = invoice_of(world, "F-2")

    assert invoice.customer is None


def test_a_pair_where_neither_side_is_an_identity_in_sql(world: World):
    """What `identified_by` can never express, because it anchors one side to an id: «the
    lines carrying this invoice's tax code», whoever they belong to.

    Both invoices are VAT; the two VAT lines belong to F-1 and F-2's own line is EXEMPT. So
    F-2 reaches F-1's lines and not its own — ownership plays no part, the pair does.
    """
    asked = Criteria(specification=Specification({"taxed_lines": Criteria()}))

    first = invoice_of(world, "F-1", asked)
    second = invoice_of(world, "F-2", asked)

    assert sorted(line.description for line in first.taxed_lines) == ["mug", "typo"]
    assert sorted(line.description for line in second.taxed_lines) == ["mug", "typo"]
    assert "desk" not in [line.description for line in second.taxed_lines]


def test_a_pair_where_neither_side_is_an_identity_over_a_bus(world: World):
    """The same declaration across a bus, and the proof that the match is the pair and not the
    identity: both invoices are in state «paid», and the only event that said «paid» belongs
    to F-1 — so F-2 reaches it too."""
    asked = Criteria(specification=Specification({"state_events": Criteria()}))

    first = invoice_of(world, "F-1", asked)
    second = invoice_of(world, "F-2", asked)

    assert [event.said for event in first.state_events] == ["paid"]
    assert [event.said for event in second.state_events] == ["paid"]
    assert second.state_events[0].entity_id == world.records["F-1"].id


def test_the_definition_publishes_both_sides_of_the_key(world: World):
    """What a client or an agent reads to know how a relation hangs together, without opening
    the mapper. The scope is absent on purpose: it is not a filter over the relation, it is
    part of what the relation means, and there is nothing a reader could do with it."""
    meta = describe(world.invoice)

    # Declared with the short form, published expanded: a client never has to know that
    # `identified_by` means the related side here and the parent side on a to-one.
    events = meta.relations["events"]
    assert (events.parent_field, events.related_field) == ("id", "entity_id")
    assert events.identified_by == "entity_id" and events.many

    customer = meta.relations["customer"]
    assert (customer.parent_field, customer.related_field) == ("customer_id", "id")
    assert customer.relation == "Customer" and not customer.many

    # Declared as a pair, published as it was written.
    taxed = meta.relations["taxed_lines"]
    assert (taxed.parent_field, taxed.related_field) == ("tax_code", "tax_code")


def test_a_caller_may_narrow_a_scoped_relation_further(world: World):
    asked = Criteria(
        specification=Specification(
            {
                "lines": Criteria(
                    where=Condition(field="amount", operator=Operator.GTE, value=100)
                )
            }
        )
    )

    assert list(invoice_of(world, "F-1", asked).lines) == []


def test_a_caller_cannot_widen_past_the_scope_on_sql_or_on_the_bus(world: World):
    """The same answer from both machines: asking for exactly what the scope excludes returns
    nothing, because the two conditions accumulate with AND. A node adds to the declaration
    and can never undo it."""
    asked = Criteria(
        specification=Specification(
            {
                "lines": Criteria(where=Condition(field="void", value=True)),
                "events": Criteria(where=Condition(field="entity_type", value="Draft")),
            }
        )
    )

    invoice = invoice_of(world, "F-1", asked)

    assert list(invoice.lines) == []
    assert list(invoice.events) == []


def test_a_page_of_aggregates_resolves_every_relation_once(two_engines: World):
    """Two invoices from one database, their events from another, and one command to the
    events bus for the whole page — the N+1 guard, across a database boundary."""
    two_engines.calls.clear()

    page = two_engines.invoices.search(two_engines.invoices_collection, EVERYTHING)
    by_number = {invoice.number: invoice for invoice in page}

    assert sorted(e.said for e in by_number["F-1"].events) == ["paid", "raised"]
    assert [e.said for e in by_number["F-2"].events] == ["raised"]
    assert [line.description for line in by_number["F-2"].lines] == ["desk"]
    assert len(two_engines.calls) == 1


def test_outside_a_unit_of_work_touching_a_relation_refuses(world: World):
    """Unchanged for every kind: a relation nobody asked for is not quietly fetched per row."""
    invoice = world.invoices.get(world.invoice, world.records["F-2"].id)
    assert invoice is not None

    with pytest.raises(RelationNotResolved):
        invoice.events  # noqa: B018
    with pytest.raises(RelationNotResolved):
        invoice.lines  # noqa: B018


def test_a_declared_order_is_the_relations_default_and_the_caller_may_override_it():
    """The other half of declaring a whole `Criteria` and not a bare filter: «newest first» is
    a property of the relation rather than something every caller repeats. Unlike the filter,
    an order the caller names replaces it — conditions accumulate, the rest is overridden."""
    world = same_database(events_scope=Criteria(order=parse_order("-said")))

    with world.invoices.context() as unit:
        invoice = unit.get(world.invoice, world.records["F-1"].id)
        assert invoice is not None
        assert [event.said for event in invoice.events] == ["raised", "paid", "drafted"]

    asked = Criteria(
        specification=Specification({"events": Criteria(order=parse_order("said"))})
    )
    said = invoice_of(world, "F-1", asked).events
    assert [event.said for event in said] == ["drafted", "paid", "raised"]


def test_a_scope_the_related_aggregate_cannot_answer_is_refused_not_dropped():
    """The one failure a declared scope must never have. A field the other side does not know
    would be dropped on the way across, and a dropped filter answers WIDER than the mapper
    declared — so it raises here instead, the way `Repository.narrowed` already does."""
    world = same_database(events_scope=Criteria(where=Condition(field="no_such", value=1)))

    with pytest.raises(ContractViolation, match="cannot answer the scope"):
        invoice_of(world, "F-1")
