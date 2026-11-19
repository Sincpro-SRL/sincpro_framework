"""A link to an object that saves nothing (`ObjectStorage.link`): the key and its end, signed with
the storage's secret — the same link for a window, refused once ended or when not made here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.data_layer.object_storage import (
    InMemoryObjectStore,
    ObjectStorage,
    PublicationRefused,
    Refusal,
)
from sincpro_framework.exceptions import ProgrammingError


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.at = datetime(2026, 11, 19, 10, 20, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


def storage(clock: Clock, secret: str | None = "the-service-secret") -> ObjectStorage:
    return ObjectStorage(InMemoryObjectStore(), secret, now=clock)


def test_a_link_opens_its_key_until_its_end_rounded_up_to_the_window():
    clock = Clock()
    files = storage(clock)

    token = files.link("photos/órgano 1.jpg", timedelta(hours=1))
    linked = files.linked(token)

    assert linked.key == "photos/órgano 1.jpg"
    # 10:20 + 1 h is 11:20, rounded up to the hour: it lives until 12:00.
    assert linked.ends_at == datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def test_the_same_object_keeps_the_same_link_through_its_window_so_a_browser_caches_it():
    clock = Clock()
    files = storage(clock)

    first = files.link("photos/1.jpg")
    clock.at += timedelta(minutes=30)

    assert files.link("photos/1.jpg") == first
    clock.at += timedelta(minutes=15)  # 11:05: the next window
    assert files.link("photos/1.jpg") != first


def test_an_ended_link_is_refused_as_expired():
    clock = Clock()
    files = storage(clock)
    token = files.link("photos/1.jpg", timedelta(minutes=10))

    clock.at += timedelta(minutes=20)

    with pytest.raises(PublicationRefused) as refused:
        files.linked(token)
    assert refused.value.reason is Refusal.EXPIRED


@pytest.mark.parametrize(
    "tampered",
    [
        lambda token: token[:-2] + "xx",  # another signature
        lambda token: "cGhvdG9zLzIuanBn" + token[token.index(".") :],  # another key
        lambda token: "garbage",
        lambda token: "",
    ],
)
def test_a_link_not_made_here_is_refused_as_forged(tampered):
    files = storage(Clock())

    with pytest.raises(PublicationRefused) as refused:
        files.linked(tampered(files.link("photos/1.jpg")))
    assert refused.value.reason is Refusal.FORGED


def test_a_link_made_with_another_secret_is_forged():
    clock = Clock()

    with pytest.raises(PublicationRefused) as refused:
        storage(clock, "another").linked(storage(clock).link("photos/1.jpg"))
    assert refused.value.reason is Refusal.FORGED


def test_without_a_secret_there_is_no_link_an_unsigned_one_would_open_any_key():
    with pytest.raises(ProgrammingError, match="secret"):
        storage(Clock(), secret=None).link("photos/1.jpg")


def test_a_link_saves_nothing_and_spends_nothing():
    files = storage(Clock())
    token = files.link("photos/1.jpg")

    assert files.linked(token) == files.linked(token)
    assert list(files.keys()) == []
