"""`Publications`: an object reachable by an id — for a time, for a number of uses, to read or to
write — saved in the context's store, a use spent atomically."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.context import InMemoryContexts
from sincpro_framework.data_layer.object_storage import Access, PublicationRefused, Refusal
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds
from sincpro_framework.data_layer.object_storage.services.publications import Publications
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


def test_publications_are_saved_in_a_store_of_its_own_when_given():
    own = InMemoryContexts()
    publications = Publications(PublicationIds("s3cr3t"), own)

    published = publish(publications)

    assert own.list_entries("SINCPRO_OBJECT_PUBLICATION") == [
        f"SINCPRO_OBJECT_PUBLICATION:{published.id.partition('.')[0]}"
    ]
