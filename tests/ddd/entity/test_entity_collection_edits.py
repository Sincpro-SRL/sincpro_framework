"""An `EntityCollection` is edited the way a `str` or a `date` is: `append`, `extend`, `remove`
and `replace` answer a new collection and never change the one they were asked. And it is the
type of a field — of an entity or of a DTO —, read from a list or from the page a wire sent.
"""

from dataclasses import dataclass, field, replace

import pytest
from pydantic import TypeAdapter

from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.entity.entity_meta import records_class, related_class


@dataclass
class Line(Entity):
    qty: int = 0


class Lines(EntityCollection[Line]):
    pass


@dataclass
class Order(Entity):
    number: str = ""
    lines: EntityCollection[Line] = field(default_factory=EntityCollection)


a, b, c = Line(qty=1), Line(qty=2), Line(qty=3)


def test_each_edit_answers_a_new_collection_and_leaves_the_one_it_was_asked():
    whole = EntityCollection.whole([a, b])

    appended = whole.append(c)
    removed = whole.remove(a)
    replaced = whole.replace(replace(a, qty=9))

    assert [one.qty for one in whole] == [1, 2] and whole.count == Count(value=2, exact=True)
    assert [one.qty for one in appended] == [1, 2, 3] and appended.count == Count(
        value=3, exact=True
    )
    assert [one.qty for one in removed] == [2] and removed.count == Count(value=1, exact=True)
    assert [one.qty for one in replaced] == [9, 2] and replaced.count == Count(
        value=2, exact=True
    )
    assert [one.qty for one in whole.extend([c, Line(qty=4)])] == [1, 2, 3, 4]
    assert whole.remove(b.id).ids == [a.id]


def test_an_edited_page_is_still_a_page_never_the_whole():
    page = EntityCollection(items=(a, b), cursor="eyJ…", count=Count(value=1000, exact=False))

    appended = page.append(c)

    assert appended.cursor == "eyJ…" and appended.is_partial
    assert appended.count == Count(value=1000, exact=False)


def test_removing_or_replacing_what_is_not_held_says_so_as_a_list_does():
    whole = EntityCollection.whole([a])

    with pytest.raises(ValueError, match="not in the collection"):
        whole.remove(b)
    with pytest.raises(ValueError, match="append adds"):
        whole.replace(b)


def test_a_collection_annotation_names_several_records_like_a_list():
    assert related_class(EntityCollection[Line]) == (Line, True)
    assert related_class(Lines) == (Line, True)
    assert records_class(list[Line]) == (Line, True)
    assert related_class(Line | None) == (Line, False)


def test_a_list_given_to_a_collection_field_is_held_as_the_whole_collection():
    order = Order(number="O-1", lines=[a, b])  # type: ignore[arg-type]

    assert isinstance(order.lines, EntityCollection)
    assert order.lines.count == Count(value=2, exact=True) and not order.lines.is_partial
    assert [one.qty for one in order.lines.append(c)] == [1, 2, 3]


def test_as_a_field_it_is_read_from_a_list_or_a_page_and_written_as_a_page():
    adapter = TypeAdapter(Order)

    from_list = adapter.validate_python({"number": "O-2", "lines": [{"qty": 4}]})
    from_page = TypeAdapter(EntityCollection[Line]).validate_python(
        {"items": [{"qty": 1}], "count": {"value": 30, "exact": True}, "cursor": "x"}
    )
    written = adapter.dump_python(Order(number="O-3", lines=[a]), mode="json")  # type: ignore[arg-type]

    assert from_list.lines.count == Count(value=1, exact=True)
    assert isinstance(from_list.lines[0], Line)
    assert from_page.is_partial and from_page.cursor == "x"
    assert written["lines"]["count"] == {"value": 1, "exact": True}
    assert written["lines"]["items"][0]["qty"] == 1
    assert isinstance(TypeAdapter(Lines).validate_python([{"qty": 5}]), Lines)


def test_a_list_held_where_a_collection_is_declared_is_written_as_the_whole_collection():
    written = TypeAdapter(EntityCollection[Line]).dump_python([a, b], mode="json")  # type: ignore[arg-type]

    assert written["count"] == {"value": 2, "exact": True} and len(written["items"]) == 2


def test_a_plain_collection_given_to_a_field_declared_as_a_subclass_becomes_it():
    page = EntityCollection(items=(a,), cursor="x", count=Count(value=9, exact=True))

    held = TypeAdapter(Lines).validate_python(page)

    assert isinstance(held, Lines) and held.cursor == "x" and held.count == page.count


def test_an_entity_with_an_init_var_and_no_post_init_of_its_own_is_built():
    from dataclasses import InitVar

    @dataclass
    class Draft(Entity):
        seed: InitVar[int] = 0
        lines: EntityCollection[Line] = field(default_factory=EntityCollection)

    draft = Draft(seed=3, lines=[a])  # type: ignore[arg-type]

    assert isinstance(draft.lines, EntityCollection) and len(draft.lines) == 1
