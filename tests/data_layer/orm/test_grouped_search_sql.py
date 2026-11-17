"""A grouped `Search` on the SQL store, the same as in memory: the groups counted by the
database, each with its first page of ids and the cursor inside it, beside the first records of
every group — the page a relation resolved through the bus reads.
"""

from dataclasses import dataclass

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import Repository, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import (
    Criteria,
    Entity,
    EntityReads,
    ResponsePaginatedQuery,
    Search,
)

from .engines import fresh


@dataclass
class Due(Entity):
    state: str = ""
    amount: int = 0


class ResponseDues(ResponsePaginatedQuery):
    dues: list[Due]


class QueryListDues(Search[Due, ResponseDues]):
    pass


dues = registry()
map_aggregates(
    dues,
    {
        Due: entity_table(
            "gs_due",
            dues.metadata,
            Column("state", Text, nullable=False),
            Column("amount", Integer, nullable=False),
        )
    },
)


def test_a_grouped_search_on_sql_answers_its_groups_and_the_first_records_of_each(engine_url):
    repository = Repository(fresh(engine_url, dues.metadata))
    repository.save(
        [Due(state="paid", amount=n) for n in range(3)] + [Due(state="due", amount=9)]
    )
    bus = UseFramework("gs-dues", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(QueryListDues)
    class DueReads(EntityReads[Due]):
        pass

    one_per_state = Criteria.model_validate(
        {
            "grouping": {
                "group_by": [{"field": "state"}],
                "measures": {"total": {"function": "sum", "field": "amount"}},
            },
            "pagination": {"limit": 1},
        }
    )
    answer = bus(QueryListDues(criteria=one_per_state), ResponseDues)

    assert answer.groups is not None
    groups = {one.value: one for one in answer.groups}
    assert {value: one.count for value, one in groups.items()} == {"paid": 3, "due": 1}
    assert groups["paid"].measures == {"total": 3}
    assert (len(groups["paid"].ids), groups["paid"].cursor is not None) == (1, True)
    assert sorted(one.state for one in answer.dues) == ["due", "paid"]
