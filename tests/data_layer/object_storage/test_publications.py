"""`Publications`: an object reachable by an id — for a time, for a number of uses, to read or to
write, and to another access once the first is spent — saved on the context's `KeyValueStore`
for its own time, a use counted atomically."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.context import KeyValueContexts
from sincpro_framework.data_layer.object_storage import (
    Access,
    Grant,
    PublicationRefused,
    Refusal,
)
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds
from sincpro_framework.data_layer.object_storage.services.publications import Publications
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.exceptions import FailureKind, ProgrammingError
from sincpro_framework.runtime.testing import ManualClock, fresh_context

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _own_context() -> Iterator[None]:
    with fresh_context():
        yield


def publications_at(clock: ManualClock) -> Publications:
    return Publications(PublicationIds("s3cr3t"), now=clock.now)


def publish(
    publications: Publications,
    access: Access = Access.READ,
    expires_in: timedelta = timedelta(hours=1),
    max_uses: int | None = None,
):
    return publications.publish(
        "report.pdf", access, expires_in, max_uses, {"filename": "r.pdf"}
    )


def refusal_of(opening) -> Refusal:
    with pytest.raises(PublicationRefused) as refused:
        opening()
    return refused.value.reason


def test_a_publication_opened_once_is_used_up_the_second_time():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications, max_uses=1)

    opened = publications.opened(published.id, Access.READ, spend=True)

    assert (opened.key, opened.uses, opened.expires_at) == (
        "report.pdf",
        1,
        NOON + timedelta(hours=1),
    )
    assert (
        refusal_of(lambda: publications.opened(published.id, Access.READ, spend=True))
        is Refusal.USED_UP
    )


def test_a_check_spends_nothing_and_says_what_the_publisher_said():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications, Access.WRITE, max_uses=1)

    checked = publications.opened(published.id, Access.WRITE, spend=False)

    assert (checked.uses, checked.metadata) == (0, {"filename": "r.pdf"})
    assert publications.opened(published.id, Access.WRITE, spend=True).uses == 1


def test_without_a_use_limit_it_opens_every_time_until_it_expires():
    clock = ManualClock(NOON)
    publications = publications_at(clock)
    published = publish(publications, expires_in=timedelta(minutes=15))

    uses = [publications.opened(published.id, Access.READ, spend=True).uses for _ in range(3)]

    assert uses == [1, 2, 3]
    clock.advance(minutes=15)
    assert (
        refusal_of(lambda: publications.opened(published.id, Access.READ, spend=False))
        is Refusal.EXPIRED
    )


def test_one_published_to_read_does_not_open_to_write():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications)

    assert (
        refusal_of(lambda: publications.opened(published.id, Access.WRITE, spend=True))
        is Refusal.WRONG_ACCESS
    )


def test_a_refusal_says_which_publication_it_refused_when_it_knows():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications, Access.WRITE, max_uses=1)
    publications.opened(published.id, Access.WRITE, spend=True)

    with pytest.raises(PublicationRefused) as refused:
        publications.opened(published.id, Access.WRITE, spend=True)

    assert refused.value.reason is Refusal.USED_UP
    assert refused.value.publication is not None
    assert (refused.value.publication.key, refused.value.publication.metadata) == (
        "report.pdf",
        {"filename": "r.pdf"},
    )


def test_a_revoked_publication_is_unknown():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications)

    publications.revoke(published.id)

    assert (
        refusal_of(lambda: publications.opened(published.id, Access.READ, spend=False))
        is Refusal.UNKNOWN
    )


def test_every_replica_on_the_context_store_opens_the_same_publication():
    clock = ManualClock(NOON)
    published = publish(publications_at(clock), max_uses=1)

    assert publications_at(clock).opened(published.id, Access.READ, spend=True).uses == 1
    assert (
        refusal_of(
            lambda: publications_at(clock).opened(published.id, Access.READ, spend=True)
        )
        is Refusal.USED_UP
    )


def test_of_many_redeems_at_once_of_one_use_exactly_one_opens():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications, max_uses=1)
    opened: list[int] = []
    refused: list[Refusal] = []

    def redeem() -> None:
        try:
            opened.append(publications.opened(published.id, Access.READ, spend=True).uses)
        except PublicationRefused as error:
            refused.append(error.reason)

    threads = [threading.Thread(target=redeem) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert opened == [1] and refused == [Refusal.USED_UP] * 7


class RecordingKeyValue(InMemoryKeyValue):
    """Memory that says how long each key was set for."""

    def __init__(self) -> None:
        super().__init__()
        self.lasting: dict[str, timedelta | None] = {}

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self.lasting[key] = ttl
        super().set(key, value, ttl)


def test_a_publication_lasts_its_own_time_and_a_day_more_in_a_store_of_its_own():
    recording = RecordingKeyValue()
    publications = Publications(PublicationIds("s3cr3t"), KeyValueContexts(recording))

    published = publish(publications, expires_in=timedelta(minutes=5))

    record = f"sincpro:object_publication:{published.id.partition('.')[0]}"
    assert recording.lasting == {record: timedelta(minutes=5) + timedelta(days=1)}


def test_uploaded_once_then_claimed_once_with_the_same_id():
    clock = ManualClock(NOON)
    publications = publications_at(clock)
    ticket = publications.publish(
        "inbox/scan.pdf",
        Access.WRITE,
        timedelta(minutes=5),
        1,
        {"filename": "scan.pdf"},
        Grant(access=Access.READ, expires_in=timedelta(hours=1), max_uses=1),
    )
    assert (
        refusal_of(lambda: publications.opened(ticket.id, Access.READ, spend=False))
        is Refusal.WRONG_ACCESS
    )

    clock.advance(minutes=4)
    uploaded = publications.opened(ticket.id, Access.WRITE, spend=True)
    previewed = publications.opened(ticket.id, Access.READ, spend=False)
    claimed = publications.opened(ticket.id, Access.READ, spend=True)

    assert (uploaded.access, uploaded.uses) == (Access.WRITE, 1)
    assert (previewed.access, previewed.uses, previewed.then) == (Access.READ, 0, None)
    assert previewed.expires_at == NOON + timedelta(minutes=4, hours=1)
    assert (claimed.uses, claimed.metadata) == (1, {"filename": "scan.pdf"})
    assert (
        refusal_of(lambda: publications.opened(ticket.id, Access.WRITE, spend=True))
        is Refusal.USED_UP
    )
    assert (
        refusal_of(lambda: publications.opened(ticket.id, Access.READ, spend=True))
        is Refusal.USED_UP
    )


def test_what_it_opens_to_runs_its_own_time_from_when_it_opened():
    clock = ManualClock(NOON)
    publications = publications_at(clock)
    ticket = publications.publish(
        "inbox/scan.pdf",
        Access.WRITE,
        timedelta(minutes=5),
        1,
        {},
        Grant(access=Access.READ, expires_in=timedelta(hours=1)),
    )
    publications.opened(ticket.id, Access.WRITE, spend=True)

    clock.advance(minutes=59)
    assert publications.opened(ticket.id, Access.READ, spend=True).uses == 1
    clock.advance(minutes=1)
    assert (
        refusal_of(lambda: publications.opened(ticket.id, Access.READ, spend=False))
        is Refusal.EXPIRED
    )


def test_a_then_on_uses_without_end_is_a_programming_error():
    publications = publications_at(ManualClock(NOON))

    with pytest.raises(ProgrammingError, match="max_uses"):
        publications.publish(
            "a.pdf",
            Access.WRITE,
            timedelta(minutes=5),
            None,
            {},
            Grant(access=Access.READ, expires_in=timedelta(hours=1)),
        )


def test_uses_without_end_are_counted_by_every_redeem_at_once():
    publications = publications_at(ManualClock(NOON))
    published = publish(publications)
    counted: list[int] = []

    def redeem() -> None:
        counted.append(publications.opened(published.id, Access.READ, spend=True).uses)

    threads = [threading.Thread(target=redeem) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(counted) == list(range(1, 9))


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        (Refusal.UNKNOWN, FailureKind.NOT_FOUND),
        (Refusal.FORGED, FailureKind.PERMISSION_DENIED),
        (Refusal.EXPIRED, FailureKind.PERMISSION_DENIED),
        (Refusal.USED_UP, FailureKind.PERMISSION_DENIED),
        (Refusal.WRONG_ACCESS, FailureKind.PERMISSION_DENIED),
    ],
)
def test_every_wire_answers_an_unknown_id_as_not_found_and_the_rest_as_denied(reason, kind):
    assert PublicationRefused(reason, "refused").failure_kind is kind
