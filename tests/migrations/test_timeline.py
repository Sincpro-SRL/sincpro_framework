"""The timeline: every chain of every context merged into one order.

Each chain keeps its own order, a step goes after the steps it `requires`, and among the steps
free to go the oldest id goes first.
"""

import pytest

from sincpro_framework.migrations import Step
from sincpro_framework.migrations.domain import new_step_id, timeline


def _step(context: str, id: str, parent: str | None, requires: tuple[str, ...] = ()) -> Step:
    return Step(
        id=id,
        context=context,
        store="main",
        parent=parent,
        message=f"{context} {id}",
        file=f"main/{id}.py",
        checksum="",
        requires=requires,
    )


def _chain(context: str, *ids: str) -> list[Step]:
    steps: list[Step] = []
    for id in ids:
        steps.append(_step(context, id, steps[-1].id if steps else None))
    return steps


def test_chains_on_one_store_interleave_by_id():
    a = _chain("a", "01", "02", "05", "06", "07")
    b = _chain("b", "03", "04", "08")

    assert [step.id for step in timeline([a, b])] == "01 02 03 04 05 06 07 08".split()


def test_a_chain_keeps_its_own_order_whatever_its_ids_say():
    # "02" was written first but merged late: it was rebased on top of "05"
    rebased = [_step("a", "05", None), _step("a", "02", "05")]

    assert [step.id for step in timeline([rebased])] == ["05", "02"]


def test_a_step_goes_after_what_it_requires_even_when_its_id_is_older():
    a = _chain("a", "01", "04")
    b = [_step("b", "02", None, requires=("a/main/04",))]

    assert [step.id for step in timeline([a, b])] == ["01", "04", "02"]


def test_requires_that_form_a_cycle_are_refused():
    a = [_step("a", "01", None, requires=("b/main/02",))]
    b = [_step("b", "02", None, requires=("a/main/01",))]

    with pytest.raises(ValueError, match="cycle"):
        timeline([a, b])


def test_requiring_a_step_nobody_has_is_refused():
    a = [_step("a", "01", None, requires=("b/main/99",))]

    with pytest.raises(ValueError, match="b/main/99"):
        timeline([a])


def test_new_ids_always_sort_after_the_previous_one():
    ids = [new_step_id() for _ in range(500)]

    assert ids == sorted(ids) and len(set(ids)) == 500
    assert all(len(one) == 32 for one in ids)
