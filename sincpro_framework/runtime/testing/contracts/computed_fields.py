"""`ComputedFieldsContract`: a computed field that has both an `sql` and a `python` answers the
same value either way.

    class TestInvoiceBalance(ComputedFieldsContract):
        entity = Invoice

        def make_store(self):
            store = Repository(database)
            with store.context() as repository:
                repository.save(Invoice(total=Decimal("100"), paid=Decimal("40")), …)
            return store

Two implementations of one value drift the day one of them is edited, and the drift shows up as
a report that is quietly different. This puts both in front of the same rows. pytest collects the
inherited `test_*` methods; nothing here imports pytest.
"""

from collections import Counter
from typing import Any

from sincpro_framework.ddd.criteria import Criteria, Grouping, Level, Pagination
from sincpro_framework.ddd.entity.computed import computations_of

EVERY_RECORD = 100_000


class ComputedFieldsContract:
    """Inherit, name the `entity`, answer `make_store` with a store that holds some of its
    records, and run with pytest."""

    entity: type

    def make_store(self) -> Any:
        """Answer a store — a `Repository` or anything with its `search` and
        `group_by_levels` — that already holds some records of `entity`."""
        raise NotImplementedError("answer a store holding records of `entity`")

    def _both(self) -> dict[str, Any]:
        """The computed fields of `entity` that have both an `sql` and a `python` answer, by
        name — the only ones with two answers to compare."""
        return {
            name: computation
            for name, computation in computations_of(self.entity).items()
            if computation.sql is not None and computation.python is not None
        }

    def test_what_the_store_computes_is_what_python_computes(self) -> None:
        """For every record the store holds (up to 100,000), each computed field the store
        answers through `sql` equals what its `python` answers for that record.

        Fails as well when `entity` has no such field or the store holds no record — a
        contract that compares nothing proves nothing.

            Invoice(total=100, paid=40): store's balance 60, python's 60   ->  passes
            store's balance 60.00, python's 60.0001
                ->  AssertionError("(id, field, the store's value, python's value):
                                    [(7, 'balance', 60.00, 60.0001)]")

        Every disagreement is listed at once, not only the first.
        """
        both = self._both()
        assert (
            both
        ), f"{self.entity.__name__} has no computed field with both `sql` and `python`"
        found = self.make_store().search(
            self.entity, Criteria(pagination=Pagination(limit=EVERY_RECORD))
        )
        assert len(found) > 0, "the store holds no records to compare"

        disagree = [
            (record.id, name, getattr(record, name), computation.python(record))
            for record in found
            for name, computation in both.items()
            if getattr(record, name) != computation.python(record)
        ]

        assert not disagree, f"(id, field, the store's value, python's value): {disagree}"

    def test_grouping_by_a_computed_field_makes_the_groups_python_computes(self) -> None:
        """Grouping by each computed field in the store makes the same groups, with the same
        counts, as counting its `python` answer over the records read.

            balances 60, 60, 0 in python   ->  buckets {60: 2, 0: 1} from the store passes
            the store groups {60: 3}       ->  AssertionError naming the field, "balance"

        One grouping per field with both answers, each compared as a whole — values and
        counts.
        """
        store = self.make_store()
        found = store.search(self.entity, Criteria(pagination=Pagination(limit=EVERY_RECORD)))

        for name, computation in self._both().items():
            expected = Counter(computation.python(record) for record in found)
            buckets = store.group_by_levels(
                self.entity,
                Criteria(grouping=Grouping(group_by=(Level(field=name),))),
            )

            assert {bucket.value: bucket.count for bucket in buckets} == dict(expected), name
