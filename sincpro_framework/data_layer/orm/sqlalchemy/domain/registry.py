"""What is mapped: the relations declared or inferred per aggregate, and the aggregates each
table holds. Filled by `map_aggregates`, read by everything that needs to know."""

from sqlalchemy import Table

from sincpro_framework.ddd.entity import relations

RELATIONS: dict[type, dict[str, relations.Relation]] = {}


def relations_of(aggregate: type) -> dict[str, relations.Relation]:
    """What was declared for this aggregate, by relation name; empty when nothing was."""
    return RELATIONS.get(aggregate, {})


_aggregates_by_table: dict[Table, list[type]] = {}


def record_mapping(table: Table, aggregate: type) -> None:
    """Notes that `aggregate` is mapped onto `table`."""
    _aggregates_by_table.setdefault(table, []).append(aggregate)


def aggregates_of(table: Table) -> list[type]:
    """The aggregates mapped onto `table` — what a statement over it reads."""
    return _aggregates_by_table.get(table, [])
