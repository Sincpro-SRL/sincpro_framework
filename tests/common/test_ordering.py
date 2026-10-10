"""`ordering`: how several things hung on the same point — hooks, interceptors, error handlers —
are put in the order they run, the one way every extension point of the framework does it.

A stable topological sort (Kahn): `before` and `after` are hard constraints, `sequence` (lower
first, like Odoo's) breaks ties, then the order they were registered. A replacement takes the
place of what it replaces; what is switched off is gone, and constraints on it with it. Only a
circle is refused: what works, only not as said, is a note for the extension point to log.
"""

import pytest

from sincpro_framework import ProgrammingError
from sincpro_framework.common.ordering import Placement, ordered


def audit(): ...


def check(): ...


def stamp(): ...


def notify(): ...


def check_v2(): ...


def check_v3(): ...


def test_with_nothing_said_they_run_in_the_order_they_were_registered():
    assert ordered([Placement(audit), Placement(check), Placement(stamp)]).items == [
        audit,
        check,
        stamp,
    ]


def test_a_lower_sequence_runs_first_and_ties_keep_the_registration_order():
    placements = [
        Placement(audit, sequence=20),
        Placement(check),
        Placement(stamp, sequence=5),
    ]

    assert ordered(placements).items == [stamp, check, audit]


def test_before_and_after_win_over_sequence():
    """Priority inheritance through the constraints: `notify` runs before `audit` (1) and
    `check` before `stamp` (1), so both inherit 1 — and among the four at 1, the order they were
    registered decides, with every constraint kept."""
    placements = [
        Placement(audit, sequence=1),
        Placement(check, sequence=50),
        Placement(stamp, sequence=1, after=(check,)),
        Placement(notify, sequence=99, before=(audit,)),
    ]

    assert ordered(placements).items == [check, stamp, notify, audit]


def test_a_cycle_is_refused_naming_it():
    placements = [Placement(audit, before=(check,)), Placement(check, before=(audit,))]

    with pytest.raises(ProgrammingError, match="audit.*check|check.*audit"):
        ordered(placements)


def test_a_replacement_takes_the_place_and_the_constraints_of_what_it_replaces():
    placements = [
        Placement(audit),
        Placement(check, sequence=5),
        Placement(stamp, after=(check,)),
        Placement(check_v2, replaces=check),
    ]

    result = ordered(placements)

    assert result.items == [check_v2, audit, stamp]
    assert result.replaced == {check_v2: (check,)}


def test_replacing_a_replaced_one_replaces_the_latest_with_a_note():
    placements = [
        Placement(check),
        Placement(check_v2, replaces=check),
        Placement(check_v3, replaces=check),
    ]

    result = ordered(placements)

    assert result.items == [check_v3]
    assert result.replaced == {check_v3: (check, check_v2)}
    assert "check_v2 already replaced" in result.notes[0]


def test_a_chain_of_replacements_is_recorded_oldest_first():
    placements = [
        Placement(check),
        Placement(check_v2, replaces=check),
        Placement(check_v3, replaces=check_v2),
    ]

    result = ordered(placements)

    assert result.items == [check_v3]
    assert result.replaced == {check_v3: (check, check_v2)}


def test_replacing_what_is_not_there_runs_on_its_own_with_a_note():
    """A core that is not installed must not break the extension written for it."""
    result = ordered([Placement(audit), Placement(check_v2, replaces=check)])

    assert result.items == [audit, check_v2]
    assert result.replaced == {}
    assert "not registered" in result.notes[0]


def test_what_is_switched_off_is_gone_and_constraints_on_it_with_it():
    placements = [Placement(audit), Placement(check), Placement(stamp, after=(check,))]

    result = ordered(placements, off=(check,))

    assert result.items == [audit, stamp]
    assert result.off == (check,)


def test_switching_off_what_is_not_there_does_nothing_with_a_note():
    result = ordered([Placement(audit)], off=(check,))

    assert result.items == [audit]
    assert "not registered" in result.notes[0]


def test_a_constraint_on_something_not_registered_is_no_constraint():
    assert ordered([Placement(audit, after=(check,))]).items == [audit]
