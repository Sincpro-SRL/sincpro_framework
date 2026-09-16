"""The shape every read shares: what a query command carries, and what a paged answer returns.

Inherited rather than repeated. A use case that reads declares what it is *about* — the records
and their type — and gets the filter on the way in and the pagination on the way out for free:

    class CommandListDatasets(Query):
        pass

    class ResponseListDatasets(ResponsePaginatedQuery):
        datasets: list[Dataset]

Without these two, every listing writes the same five fields again and the sixth one writes
them slightly differently.
"""

from typing import Any

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity_collection import Count, Dropped, EntityCollection
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.model_meta import Meta
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Query(DataTransferObject):
    """What any command that reads carries.

    class CommandListDatasets(Query):   →   CommandListDatasets(criteria=Criteria(...))
        pass
    """

    criteria: Criteria = Criteria()
    """Everything being asked: the filter, the ordering, the page and what to bring along.

    One field rather than a parameter per filter, because a filter *is* a criteria — what the
    interface calls a saved reading is the same object, merged."""


class ResponsePaginatedQuery(DataTransferObject):
    """What any paged answer returns beside its records.

        class ResponseListDatasets(ResponsePaginatedQuery):
            datasets: list[Dataset]         →   .datasets .cursor .count .model_meta_data

    The records keep the subclass's own name — `datasets`, `runs`, `plans` — because a reader
    of a response should see what it is about, not `items`.
    """

    cursor: str | None = None
    """Where the next page starts. `None` means there is no next one."""

    count: Count | None = None
    """How many match in total, capped unless the criteria asked for the exact number.
    `exact=False` means *at least* that many."""

    model_meta_data: Meta | None = None
    """What may be filtered, ordered and walked, and with which operators, so a client can
    build a filter form, a column menu or a URL without carrying a schema.

    Travels with every answer unless the criteria said `meta=false` — a caller that already
    knows the model, and is paging, can turn it off."""

    dropped: list[Dropped] = []
    """Conditions the model could not answer — an unknown field, an operator its type does not
    take. Reported rather than raised: a shared link outlives the schema it was written
    against, and a dropped filter always widens the result."""

    @classmethod
    def records_field(cls) -> str:
        """The one field the subclass added: where its records go.

            class ResponseListDatasets(ResponsePaginatedQuery):
                datasets: list[Dataset]     →   out  'datasets'

        A convention rather than a declaration, the same way the first field of an aggregate
        is its identity. Anything but exactly one added field is refused, because then nobody
        — including this method — can tell which one holds the records.
        """
        own = [
            name
            for name in cls.model_fields
            if name not in ResponsePaginatedQuery.model_fields
        ]
        if len(own) != 1:
            raise ContractViolation(
                f"{cls.__name__} declares {len(own)} fields of its own ({', '.join(own) or 'none'}); "
                "a paginated response declares exactly one, holding its records"
            )
        return own[0]

    @classmethod
    def of(cls, page: EntityCollection, criteria: Criteria) -> Any:
        """A page as the answer that goes out.

            in      EntityCollection(20 records of 197, more), the criteria it answered
            out     ResponseListDatasets(datasets=[...], cursor='eyJ…', count=Count(197, True))

        The page already carries the definition of the model it came from — the engine read it
        to answer — so a use case never has to know that describing a model is a thing that
        exists. The criteria only says whether the definition was asked for.

        >>> ResponseListDatasets.of(page, criteria).datasets[0].name
        'labs.csv'
        """
        return cls(
            **{cls.records_field(): list(page.items)},
            cursor=page.cursor,
            count=page.count,
            model_meta_data=page.meta if criteria.meta else None,
            dropped=list(page.dropped),
        )
