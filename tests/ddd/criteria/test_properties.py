"""The laws the query vocabulary holds to, over inputs nobody chose.

These are worth generating rather than enumerating because the failures are combinatorial:
a set operation that loses its order does so for *some* overlaps, a merge that forgets a field
does so for *some* pairs, and an example-based test finds whichever case its author happened
to imagine.

Three families, and each one guards something that would be expensive to discover later.
Ordering stability, because a list that reorders itself between two identical requests cannot
be paged. Metadata dropping, because a leftover cursor points into a result set that no longer
exists. And the cursor round trip, because a keyset value that comes back as a different type
moves the page boundary without anybody noticing.
"""

from dataclasses import dataclass
from datetime import datetime

from hypothesis import given
from hypothesis import strategies as st

from sincpro_framework.ddd.criteria import Condition, Criteria, conditions_of
from sincpro_framework.ddd.criteria.pagination import CursorKeys
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection


@dataclass
class Row:
    row_id: str


IDS = st.lists(st.text(min_size=1, max_size=3), max_size=8, unique=True)

KEYS = st.lists(
    st.one_of(
        st.text(max_size=8),
        st.integers(),
        st.booleans(),
        st.none(),
        st.datetimes(),
    ),
    max_size=4,
)


@st.composite
def collections(draw) -> EntityCollection[Row]:
    ids = draw(IDS)
    return EntityCollection(items=tuple(Row(row_id=one) for one in ids))


@given(collections(), collections())
def test_union_is_commutative_in_membership(left, right):
    """Membership commutes even though order does not — union keeps the left side first."""
    assert set((left | right).ids) == set((right | left).ids)


@given(collections(), collections())
def test_union_keeps_the_left_operand_in_front(left, right):
    assert (left | right).ids[: len(left)] == left.ids


@given(collections())
def test_union_and_intersection_with_itself_change_nothing(one):
    assert (one | one).ids == one.ids
    assert (one & one).ids == one.ids


@given(collections(), collections())
def test_intersection_commutes_in_membership(left, right):
    assert set((left & right).ids) == set((right & left).ids)


@given(collections(), collections())
def test_difference_removes_exactly_the_other_side(left, right):
    assert set((left - right).ids) == set(left.ids) - set(right.ids)


@given(collections(), collections())
def test_symmetric_difference_is_both_differences(left, right):
    assert set((left ^ right).ids) == set(left.ids) ^ set(right.ids)


@given(collections(), collections())
def test_every_set_operation_returns_something_that_is_not_a_page(left, right):
    left = EntityCollection(
        items=left.items, cursor="token", count=Count(value=99, exact=False)
    )

    for derived in (left | right, left & right, left - right, left ^ right):
        assert derived.cursor is None
        assert derived.count is None


@given(st.integers(min_value=1, max_value=200), st.booleans())
def test_a_count_is_partial_exactly_when_it_does_not_cover_what_is_held(value, exact):
    held = EntityCollection(items=(Row("a"), Row("b")), count=Count(value=value, exact=exact))

    assert held.is_partial == (not exact or value > 2)


@given(KEYS, st.text(max_size=12))
def test_a_cursor_round_trips_whatever_it_was_given(keys, signature):
    assert CursorKeys.read(
        CursorKeys(keys=tuple(keys), ordering=signature).token(), signature
    ).keys == tuple(keys)


@given(KEYS)
def test_a_cursor_keeps_the_type_of_every_key(keys):
    restored = CursorKeys.read(
        CursorKeys(keys=tuple(keys), ordering="-id").token(), "-id"
    ).keys

    assert [type(key) for key in restored] == [type(key) for key in keys]


@given(st.lists(st.text(min_size=1, max_size=4), max_size=5, unique=True))
def test_merging_criteria_keeps_every_condition(names):
    merged = Criteria()
    for name in names:
        merged = merged.merged_with(Criteria(where=Condition(field=name, value=1)))

    assert [leaf.field for leaf in conditions_of(merged.expression)] == names


@given(st.datetimes())
def test_a_datetime_key_survives_with_its_microseconds(moment: datetime):
    assert CursorKeys.read(
        CursorKeys(keys=(moment,), ordering="-at").token(), "-at"
    ).keys == (moment,)
