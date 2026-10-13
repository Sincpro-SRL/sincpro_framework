"""The ANSWER an extended entity gives a client: flat or nested, and its schema.
Requirements R4, R9 of the specification."""

from sincpro_framework.ddd import Criteria, ResponsePaginatedQuery
from sincpro_framework.ddd.criteria import Condition, Specification

from .world import World


def answer_for(k, store, key: str, specification: Specification | None = None) -> dict:
    class ResponseMoves(ResponsePaginatedQuery):
        moves: list[k.Move]  # type: ignore[name-defined]

    criteria = Criteria(where=Condition(field="id", value=key), specification=specification)
    page = store.search(k.Move, criteria)
    written = ResponseMoves.of(page, criteria).model_dump(mode="json")["moves"][0]
    return {
        name: value
        for name, value in written.items()
        if name not in ("created_at", "updated_at", "version")
    }


class ExtendedAnswers(World):
    def test_an_answer_writes_the_fields_of_an_extension_flat_by_default(self) -> None:
        k, store = self.populated()

        written = answer_for(k, store, "C")

        assert (written["route"], written["carrier"]) == ("R7", "DHL")
        assert "delivery" not in written

    def test_an_answer_nests_each_extension_under_its_key_when_the_entity_delegates(
        self,
    ) -> None:
        k, store = self.populated(delegated=True)

        tracked, plain = answer_for(k, store, "C"), answer_for(k, store, "A")

        assert tracked["delivery"] == {"route": "R7"} and tracked["tracking"] == {
            "carrier": "DHL"
        }
        assert "route" not in tracked and "carrier" not in tracked
        assert (
            plain["delivery"] is None and plain["tracking"] is None
        )  # the shape does not move

    def test_a_specification_that_names_a_field_of_an_extension_brings_it(self) -> None:
        k, store = self.populated()

        narrowed = Specification({"number": Criteria(), "route": Criteria()})

        assert answer_for(k, store, "B", narrowed)["route"] == "R7"
        assert "route" not in answer_for(k, store, "B", Specification({"number": Criteria()}))

    def test_the_schema_of_an_answer_describes_the_fields_of_the_extensions(self) -> None:
        k, store = self.make_world()

        class ResponseMoves(ResponsePaginatedQuery):
            moves: list[k.Move]  # type: ignore[name-defined]

        items = ResponseMoves.model_json_schema(mode="serialization")["properties"]["moves"][
            "items"
        ]

        assert {"route", "carrier", "cufd"} <= set(items["properties"])

    def test_the_schema_of_a_delegated_answer_describes_one_key_per_extension(self) -> None:
        k, store = self.make_world(delegated=True)

        class ResponseMoves(ResponsePaginatedQuery):
            moves: list[k.Move]  # type: ignore[name-defined]

        items = ResponseMoves.model_json_schema(mode="serialization")["properties"]["moves"][
            "items"
        ]

        assert {"delivery", "tracking", "fiscal"} <= set(items["properties"])
        assert "route" not in items["properties"]
