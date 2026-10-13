"""READING an extended entity, for any store. Requirements R1, R5 of the specification."""

from sincpro_framework.ddd import Condition, Criteria

from .world import World


class ExtendedReads(World):
    def test_reading_the_base_answers_each_record_as_the_extension_it_has(self) -> None:
        k, store = self.populated()

        found = {one.id: type(one) for one in store.search(k.Move, Criteria())}

        assert found == {
            "A": k.Move,
            "B": k.MoveDelivery,
            "C": k.MoveTracking,
            "E": k.MoveFiscal,
        }

    def test_the_fields_of_the_extension_come_with_the_record_read_as_the_base(self) -> None:
        k, store = self.populated()

        tracking = store.get(k.Move, "C")

        assert (tracking.number, tracking.route, tracking.carrier) == ("F-C", "R7", "DHL")

    def test_reading_an_extension_answers_it_and_what_extends_it_and_nothing_else(
        self,
    ) -> None:
        k, store = self.populated()

        found = {one.id: type(one) for one in store.search(k.MoveDelivery, Criteria())}

        assert found == {
            "B": k.MoveDelivery,
            "C": k.MoveTracking,
        }  # not A (base), not E (sibling)

    def test_the_base_is_filtered_by_a_field_only_an_extension_has(self) -> None:
        k, store = self.populated()

        found = store.search(k.Move, Criteria(where=Condition(field="route", value="R7")))

        assert sorted(one.id for one in found) == ["B", "C"] and not found.dropped

    def test_the_base_counts_every_record_whatever_it_is(self) -> None:
        k, store = self.populated()

        assert len(store.search(k.Move, Criteria())) == 4

    def test_the_definition_of_the_base_says_whose_each_field_is(self) -> None:
        k, store = self.populated()

        fields = store.search(k.Move, Criteria()).meta.fields

        assert fields["number"].owner is None
        assert (fields["route"].owner, fields["carrier"].owner, fields["cufd"].owner) == (
            k.MoveDelivery.__name__,
            k.MoveTracking.__name__,
            k.MoveFiscal.__name__,
        )
