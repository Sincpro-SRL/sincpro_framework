"""WRITING an extended entity, for any store. Requirement R6 of the specification."""

from sincpro_framework.ddd import Criteria

from .world import World


class ExtendedWrites(World):
    def test_a_record_saved_as_an_extension_is_read_back_whole_as_the_base(self) -> None:
        k, store = self.make_world()
        store.save(k.MoveTracking(id="T", number="F-T", route="R1", carrier="UPS"))

        read = store.get(k.Move, "T")

        assert (type(read), read.number, read.route, read.carrier) == (
            k.MoveTracking,
            "F-T",
            "R1",
            "UPS",
        )

    def test_a_record_read_as_the_base_and_edited_keeps_its_extension(self) -> None:
        k, store = self.populated()
        record = store.get(k.Move, "B")
        record.number = "F-B2"

        store.save(record)

        again = store.get(k.Move, "B")
        assert (type(again), again.number, again.route) == (k.MoveDelivery, "F-B2", "R7")

    def test_removing_a_record_removes_it_for_every_way_of_reading_it(self) -> None:
        k, store = self.populated()

        store.remove(store.get(k.Move, "C"))

        assert store.get(k.Move, "C") is None
        assert store.get(k.MoveTracking, "C") is None
        assert sorted(one.id for one in store.search(k.MoveDelivery, Criteria())) == ["B"]

    def test_a_stored_base_is_given_an_extension_and_keeps_what_it_had(self) -> None:
        k, store = self.populated()
        plain = store.get(k.Move, "A")

        store.extend(plain, k.MoveDelivery)

        promoted = store.get(k.Move, "A")
        assert type(promoted) is k.MoveDelivery and promoted.number == "F-A"
        assert len(store.search(k.Move, Criteria())) == 4  # not a second record
