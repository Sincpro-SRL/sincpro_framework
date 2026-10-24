"""EDITING an extended entity through the commands written for its base, for any store.
Requirements R1, R6 of the specification: the commands of the core, unchanged, create and edit what
a module added."""

import pytest

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.ddd import Get, ResponsePaginatedQuery, ResponseRecord, Search
from sincpro_framework.ddd.editing.edits import Edit, EditAction, EntityEdits, ResponseEdit
from sincpro_framework.store.adapters.stores import InMemoryContexts

from .world import World


class Forms:
    """The commands of the base, written for it alone and bound to a store."""

    def __init__(self, k, store) -> None:
        Move = k.Move

        class ResponseMove(ResponseRecord):
            move: Move  # type: ignore[valid-type]

        class ResponseMoves(ResponsePaginatedQuery):
            moves: list[Move]  # type: ignore[valid-type]

        class ResponseEditMove(ResponseEdit):
            move: Move  # type: ignore[valid-type]

        class QueryGetMove(Get[Move, ResponseMove]):  # type: ignore[valid-type]
            pass

        class QuerySearchMoves(Search[Move, ResponseMoves]):  # type: ignore[valid-type]
            pass

        class CommandEditMove(Edit[Move, ResponseEditMove]):  # type: ignore[valid-type]
            pass

        bus = UseFramework("accounting", log_after_execution=False)
        bus.add_dependency("repository", store)
        bus.store_context_in(InMemoryContexts())

        @bus.feature([QueryGetMove, QuerySearchMoves, CommandEditMove])
        class MoveForms(EntityEdits[Move]):  # type: ignore[valid-type]
            pass

        self.bus, self.Response = bus, ResponseEditMove
        self.Get, self.ResponseMove, self.Edit = QueryGetMove, ResponseMove, CommandEditMove

    def save(self, key: str, values: dict, **more):
        with self.bus.context({"user_id": "u1"}):
            return self.bus(
                self.Edit(id=key, values=values, action=EditAction.SAVE, **more),
                self.Response,
            )

    def save_draft(self, key: str, values: dict, **more):
        with self.bus.context({"user_id": "u1"}):
            return self.bus(self.Edit(id=key, values=values, **more), self.Response)

    def read(self, key: str) -> dict:
        return self.bus(self.Get(id=key), self.ResponseMove).model_dump(mode="json")["move"]


class ExtendedEdits(World):
    def test_the_same_get_of_the_base_returns_the_extension_flat_with_nothing_declared(
        self,
    ) -> None:
        k, store = self.populated()
        forms = Forms(k, store)

        body = forms.read("B")

        assert (body["number"], body["route"]) == ("F-B", "R7")

    def test_a_new_record_with_the_fields_of_an_extension_is_created_as_that_extension(
        self,
    ) -> None:
        k, store = self.make_world()

        Forms(k, store).save("N", {"number": "F-N", "route": "R1", "carrier": "UPS"})

        made = store.get(k.Move, "N")
        assert type(made) is k.MoveTracking and (made.route, made.carrier) == ("R1", "UPS")

    def test_a_stored_record_without_the_extension_is_given_it_by_an_edit_that_names_its_fields(
        self,
    ) -> None:
        k, store = self.populated()

        Forms(k, store).save("A", {"route": "R5"})

        after = store.get(k.Move, "A")
        assert type(after) is k.MoveDelivery and (after.route, after.number) == ("R5", "F-A")

    def test_fields_of_sibling_extensions_that_no_class_holds_are_refused_naming_them(
        self,
    ) -> None:
        k, store = self.make_world()

        with pytest.raises(exceptions.ClientError, match="no one class holds"):
            Forms(k, store).save("X", {"route": "R1", "cufd": "C1"})

    def test_an_edit_kept_as_a_draft_stores_nothing(self) -> None:
        k, store = self.populated()

        answer = Forms(k, store).save_draft("B", {"route": "R3"})

        assert answer.move.route == "R3"  # type: ignore[attr-defined]
        assert store.get(k.Move, "B").route == "R7"


class DelegatedEdits(World):
    """A form of an entity that delegates reads the record nested and sends it back as it read it."""

    def test_a_form_that_read_a_delegated_record_sends_it_back_as_it_read_it(self) -> None:
        k, store = self.populated(delegated=True)
        forms = Forms(k, store)
        values = {
            name: value
            for name, value in forms.read("B").items()
            if name not in ("id", "created_at", "updated_at", "version")
        }
        assert values["delivery"] == {"route": "R7"} and values["tracking"] is None
        values["number"], values["delivery"]["route"] = "F-B2", "R-NEW"

        forms.save("B", values)

        stored = store.get(k.Move, "B")
        assert (stored.number, stored.route) == ("F-B2", "R-NEW")

    def test_an_extension_the_record_does_not_have_writes_nothing_when_sent_null(
        self,
    ) -> None:
        k, store = self.populated(delegated=True)

        Forms(k, store).save("A", {"number": "F-A2", "delivery": None, "tracking": None})

        after = store.get(k.Move, "A")
        assert type(after) is k.Move and after.number == "F-A2"

    def test_a_delegated_form_creates_a_record_as_the_extension_its_nested_values_name(
        self,
    ) -> None:
        k, store = self.make_world(delegated=True)

        Forms(k, store).save(
            "N",
            {"number": "F-N", "delivery": {"route": "R1"}, "tracking": {"carrier": "UPS"}},
        )

        made = store.get(k.Move, "N")
        assert type(made) is k.MoveTracking and (made.route, made.carrier) == ("R1", "UPS")

    def test_flat_values_are_taken_as_they_are_whatever_the_entity_delegates(self) -> None:
        k, store = self.populated(delegated=True)

        Forms(k, store).save("B", {"route": "R-FLAT"})

        assert store.get(k.Move, "B").route == "R-FLAT"

    def test_a_nested_value_with_a_field_the_extension_does_not_have_is_refused_naming_it(
        self,
    ) -> None:
        k, store = self.populated(delegated=True)

        with pytest.raises(exceptions.ClientError, match="delivery has no field cufd"):
            Forms(k, store).save("B", {"delivery": {"cufd": "X"}})

    def test_an_entity_that_does_not_delegate_does_not_take_its_extensions_nested(
        self,
    ) -> None:
        k, store = self.populated()

        with pytest.raises(exceptions.ClientError, match="no field delivery"):
            Forms(k, store).save("B", {"delivery": {"route": "R1"}})

    def test_the_answer_to_an_edit_nests_the_extensions_when_the_entity_delegates(
        self,
    ) -> None:
        k, store = self.populated(delegated=True)

        answer = Forms(k, store).save_draft("B", {"delivery": {"route": "R3"}})

        shown = answer.model_dump(mode="json")["move"]
        assert shown["delivery"] == {"route": "R3"} and "route" not in shown

    def test_the_fields_a_form_reports_changed_are_taken_nested_or_flat(self) -> None:
        k, store = self.populated(delegated=True)
        forms = Forms(k, store)

        version = 0
        for changed in (["delivery"], ["delivery.route"], ["route"]):
            answer = forms.save_draft(
                "B", {"delivery": {"route": "R3"}}, changed=changed, version=version
            )
            version = answer.draft.version  # type: ignore[union-attr]
            assert answer.move.route == "R3"  # type: ignore[attr-defined]
