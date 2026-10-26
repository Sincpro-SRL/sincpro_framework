"""What is mapped in this process: the relations declared or inferred per aggregate, and the
aggregates each table holds. Filled by `map_aggregates`, read by everything that needs to know.

    mapping_registry.relations_of(Dataset)       ->  {"runs": TableRelation(...)}
    mapping_registry.aggregates_of(dataset)      ->  [Dataset]
"""

from sqlalchemy import Table

from sincpro_framework.ddd.entity import relations


class MappingRegistry:
    """The process's record of what `map_aggregates` mapped — one instance,
    `mapping_registry`, because a class is mapped once per process and every repository on
    every database reads the same mapping."""

    def __init__(self) -> None:
        self._relations: dict[type, dict[str, relations.Relation]] = {}
        self._aggregates_by_table: dict[Table, list[type]] = {}

    def relations_of(self, aggregate: type) -> dict[str, relations.Relation]:
        """What was declared for this aggregate, by relation name; empty when nothing was."""
        return self._relations.get(aggregate, {})

    def add_relation(self, aggregate: type, name: str, relation: relations.Relation) -> bool:
        """Registers `relation` under `name` unless one is registered there already: the first
        declaration wins.

            add_relation(Dataset, "runs", declared)   ->  True     registered
            add_relation(Dataset, "runs", another)    ->  False    the first one stands
        """
        known = self._relations.setdefault(aggregate, {})
        if name in known:
            return False
        known[name] = relation
        return True

    def record_mapping(self, table: Table, aggregate: type) -> None:
        """Notes that `aggregate` is mapped onto `table`."""
        self._aggregates_by_table.setdefault(table, []).append(aggregate)

    def aggregates_of(self, table: Table) -> list[type]:
        """The aggregates mapped onto `table` — what a statement over it reads."""
        return self._aggregates_by_table.get(table, [])


mapping_registry = MappingRegistry()
"""The one registry of the process."""
