"""Shapes a project reaches for, answered with what the layer already has.

Each of these was written somewhere else first — a hand-rolled `sqlite3` store, a custom column
type, a refusal the aggregate enforced by hand — because it did not look like the layer could do
it. Each one can. They are here so the next project finds the answer instead of the workaround,
and so a change that quietly takes one away fails.
"""

from dataclasses import dataclass, field

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import Rule
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

# --- an aggregate whose identity is two columns --------------------------------------------


@dataclass
class RowLineage:
    """Identified by a pair, the way a ledger keyed on `(fingerprint, lineage)` is. Not an
    `Entity`: that convention mints one id, and this row's identity is the two facts about it.
    """

    table_fingerprint: str = ""
    lineage_id: str = ""
    position: int = 0


class Lineages(EntityCollection[RowLineage]):
    pass


# --- a fact that is written once ------------------------------------------------------------


@dataclass
class Claim(Entity):
    fingerprint: str = ""
    verdict: str = ""


# --- a list column on a row written before the field existed --------------------------------


@dataclass
class Profile(Entity):
    columns: list[str] = field(default_factory=list)


recipes_registry = registry()
metadata = recipes_registry.metadata

lineage_table = sa.Table(
    "row_lineage",
    metadata,
    sa.Column("table_fingerprint", sa.Text, primary_key=True),
    sa.Column("lineage_id", sa.Text, primary_key=True),
    sa.Column("position", sa.Integer),
)
claim_table = entity_table(
    "claim", metadata, sa.Column("fingerprint", sa.Text), sa.Column("verdict", sa.Text)
)
profile_table = entity_table("profile", metadata, sa.Column("columns", JsonText))

map_aggregates(
    recipes_registry,
    {RowLineage: lineage_table, Claim: claim_table, Profile: profile_table},
)


@pytest.fixture
def store() -> Repository:
    database = Database("sqlite://")
    metadata.create_all(database.engine)
    return Repository(database)


def test_an_aggregate_identified_by_two_columns_is_stored_and_read_like_any_other(store):
    """A composite key needs no special store. Three hand-rolled `sqlite3` stores were written
    elsewhere on the belief that it did — thread-local connections, hand-written DDL and a
    homegrown migrator, for something the repository already answers."""
    store.save(
        [
            RowLineage(table_fingerprint="fp1", lineage_id="a", position=1),
            RowLineage(table_fingerprint="fp1", lineage_id="b", position=2),
            RowLineage(table_fingerprint="fp2", lineage_id="a", position=1),
        ]
    )

    mine = Criteria(where=Condition(field="table_fingerprint", value="fp1"))
    assert sorted(one.lineage_id for one in store.fetch_all(Lineages, mine)) == ["a", "b"]
    assert store.count(Lineages).value == 3


def test_the_identity_of_a_composite_key_is_the_tuple(store):
    """`get` takes the identity, and here the identity is the pair — in the order the table
    declares its primary key."""
    store.save(RowLineage(table_fingerprint="fp1", lineage_id="a", position=1))

    found = store.get(RowLineage, ("fp1", "a"))

    assert found is not None and found.position == 1
    assert store.get(RowLineage, ("fp1", "nobody")) is None


def test_one_value_for_a_two_column_key_is_refused_by_the_engine(store):
    """Not a gap — a question that does not identify anything."""
    store.save(RowLineage(table_fingerprint="fp1", lineage_id="a", position=1))

    with pytest.raises(Exception, match="[Ii]ncorrect number of values"):
        store.get(RowLineage, "fp1")


def test_written_once_is_a_rule_and_not_a_mode_the_store_has(store):
    """An append-only aggregate — a ledger, an audit record — is four lines beside the wiring.
    A second recording of the same fact is either identical or a contradiction, and replacing it
    would silently accept the second."""

    def written_once(record: Claim) -> None:
        if not record.is_new:
            raise ContractViolation(
                f"{type(record).__name__} is written once and never replaced"
            )

    store._rules = (Rule(entity=Claim, before_save=written_once),)
    claim = Claim(fingerprint="fp1", verdict="600")
    store.save(claim)

    claim.verdict = "something else"
    with pytest.raises(ContractViolation, match="written once"):
        store.save(claim)

    stored = store.get(Claim, claim.id)
    assert stored is not None and stored.verdict == "600"


def test_a_list_column_needs_no_column_type_of_its_own(store):
    """A custom `JsonTextList` was written elsewhere for exactly this: a row from before the
    field existed holds NULL, and the domain got `None` where it declares `list[str]`."""
    import sqlalchemy as sa

    profile = Profile(columns=["a", "b"])
    store.save(profile)
    assert store.get(Profile, profile.id).columns == ["a", "b"]  # type: ignore[union-attr]

    with store.database.session() as session:
        session.execute(sa.text("UPDATE profile SET columns = NULL"))

    stored = store.get(Profile, profile.id)
    assert stored is not None and stored.columns == []
