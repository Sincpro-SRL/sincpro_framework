"""An event's `name` travels with it and is never blank, and a worker rebuilds what it receives by
the one tolerant rule every receiver follows: what it does not know is ignored, what it needs and
did not get is said out loud, naming the event (PRD_29, G5, G12 and G20)."""

import json
from dataclasses import dataclass

import pytest
from structlog.testing import capture_logs

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.event_driven import Subscriber
from sincpro_framework.event_driven.adapters.background_queue import STOP, _consume

from .models import TicketClosed, notifying_bus


class Inbox:
    """What the worker reads from, in the same process: the items, then the stop."""

    def __init__(self, *items: tuple) -> None:
        self.items = [*items, STOP]

    def get(self) -> tuple:
        return self.items.pop(0)


def sent(event: DomainEvent, *removed: str, **changed: object) -> tuple:
    """What `BackgroundQueue.put` hands the worker, as another deployment may have written it."""
    data = {**json.loads(event.as_json()), **changed}
    for name in removed:
        del data[name]
    return (json.dumps(data), {})


# ── G12: the name travels with the event ────────────────────────────────────────────────


def test_an_event_s_json_says_which_event_it_is():
    event = TicketClosed(reason="duplicate")

    written = json.loads(event.as_json())

    assert written["name"] == "TicketClosed"
    assert TicketClosed.from_json(event.as_json()) == event
    assert TicketClosed.from_json(written) == event


# ── G20: a name is never blank ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_name_is_refused_when_the_class_is_declared(blank):
    with pytest.raises(ContractViolation) as refused:

        @dataclass(kw_only=True)
        class Nameless(DomainEvent):
            name = blank

    assert "Nameless" in str(refused.value)


# ── G5: the background worker rebuilds tolerantly ───────────────────────────────────────


def test_the_worker_ignores_a_field_a_newer_sender_added():
    heard: list = []

    _consume(
        Inbox(sent(TicketClosed(reason="duplicate"), priority="high")),
        lambda: Subscriber(notifying_bus(heard)),
    )

    assert heard == ["duplicate"]


def test_the_worker_names_the_event_it_cannot_rebuild_and_goes_on():
    heard: list = []
    unfit = TicketClosed(reason="lost")
    shaped = sent(unfit, "reason")

    with capture_logs() as logs:
        _consume(
            Inbox(shaped, sent(TicketClosed(reason="next"))),
            lambda: Subscriber(notifying_bus(heard)),
        )

    assert heard == ["next"]
    [line] = [line for line in logs if line["log_level"] == "error"]
    assert unfit.id in line["event"]
    assert "TicketClosed" in line["event"]
