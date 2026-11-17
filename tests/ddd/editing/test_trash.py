"""A trash, built in: `Archive` puts a record away, `Restore` brings it back, and a `Purge` a cron
of the project runs deletes what stayed archived long enough. Each step is a fact of the record's
history, and none runs unless the project declares its command.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    ArchivableMixin,
    Archive,
    ChangeTrackingMixin,
    Delete,
    DomainEvent,
    DomainEvents,
    Entity,
    EntityEdits,
    Purge,
    ResponsePaginatedQuery,
    Restore,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.entity.entity import utc_now
from sincpro_framework.sincpro_abstractions import Preview


@dataclass
class Banner(ArchivableMixin, ChangeTrackingMixin, Entity):
    title: str = ""
    picture: str = ""


@dataclass
class Note(Entity):
    text: str = ""


class CommandTrashBanner(Archive[Banner]):
    pass


class CommandRestoreBanner(Restore[Banner]):
    pass


class CommandPurgeBanners(Purge[Banner]):
    pass


class CommandDeleteBanner(Delete[Banner]):
    pass


class CommandTrashNote(Archive[Note]):
    pass


class ResponseBannerEvents(ResponsePaginatedQuery):
    events: list[DomainEvent]


class QueryBannerEvents(DomainEvents[Banner, ResponseBannerEvents]):
    pass


@dataclass
class Site:
    bus: UseFramework
    repository: MemoryRepository
    files_deleted: list[str]

    def __call__(self, dto) -> object:
        return self.bus(dto)

    def history(self, key: str) -> list[str]:
        page = self.bus(QueryBannerEvents(id=key), ResponseBannerEvents)
        return [type(one).__name__ for one in page.events]

    def archived(self) -> list[str]:
        named = Criteria(
            selection=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
        )
        return sorted(one.title for one in self.repository.search(Banner, named).items)


@pytest.fixture
def site() -> Site:
    bus = UseFramework("site", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    files_deleted: list[str] = []

    @bus.feature(
        [
            CommandTrashBanner,
            CommandRestoreBanner,
            CommandPurgeBanners,
            CommandDeleteBanner,
            QueryBannerEvents,
            CommandTrashNote,
        ]
    )
    class BannerForms(EntityEdits[Banner]):
        def deleting(self, banner: Banner, dto, /) -> None:
            self.repository.after_commit(lambda: files_deleted.append(banner.picture))

        def archiving(self, banner: Banner, dto, /) -> None:
            if banner.title == "pinned":
                raise ddd_exceptions.DomainError("a pinned banner stays")

    return Site(bus, repository, files_deleted)


def a_banner(site: Site, title: str, archived_days_ago: int | None = None) -> Banner:
    banner = Banner(title=title, picture=f"{title}.png")
    if archived_days_ago is not None:
        banner.archived_at = utc_now() - timedelta(days=archived_days_ago)
    site.repository.save(banner)
    return banner


def test_a_trashed_record_leaves_every_reading_and_comes_back_when_restored(site: Site):
    banner = a_banner(site, "summer")

    assert site(CommandTrashBanner(id=banner.id)) is None
    assert site.repository.get(Banner, banner.id) is None
    assert site.archived() == ["summer"]

    assert site(CommandRestoreBanner(id=banner.id)) is None
    assert site.repository.get(Banner, banner.id) is not None
    assert site.history(banner.id) == ["EntityArchived", "EntityRestored"]


def test_a_dry_run_of_each_step_says_what_it_would_do_and_does_nothing(site: Site):
    live = a_banner(site, "summer")
    a_banner(site, "old", archived_days_ago=40)

    trash = site(CommandTrashBanner(id=live.id, dry_run=True))
    purge = site(CommandPurgeBanners(older_than=timedelta(days=30), dry_run=True))

    assert isinstance(trash, Preview) and trash.text == f"Would archive Banner {live.id}"
    assert isinstance(purge, Preview) and purge.data == {"count": 1}
    assert purge.text == "Would purge 1 Banner archived more than 30 days ago"
    assert site.repository.get(Banner, live.id) is not None
    assert site.archived() == ["old"]


def test_restoring_a_record_that_is_not_in_the_trash_is_not_found(site: Site):
    banner = a_banner(site, "summer")

    with pytest.raises(ddd_exceptions.AggregateNotFound, match="is not archived"):
        site(CommandRestoreBanner(id=banner.id))


def test_a_purge_deletes_what_stayed_archived_long_enough_and_keeps_the_rest(site: Site):
    old = [a_banner(site, f"old-{n}", archived_days_ago=40) for n in range(3)]
    a_banner(site, "recent", archived_days_ago=5)
    a_banner(site, "live")

    assert site(CommandPurgeBanners(older_than=timedelta(days=30), batch_size=2)) is None

    assert site.archived() == ["recent"]
    assert sorted(site.files_deleted) == ["old-0.png", "old-1.png", "old-2.png"]
    assert all(site.history(one.id) == ["EntityDeleted"] for one in old)


def test_the_domain_may_refuse_a_record_the_trash(site: Site):
    pinned = a_banner(site, "pinned")

    with pytest.raises(ddd_exceptions.DomainError, match="stays"):
        site(CommandTrashBanner(id=pinned.id))

    assert site.repository.get(Banner, pinned.id) is not None
    assert site.history(pinned.id) == []


def test_a_direct_deletion_is_kept_in_the_history_too(site: Site):
    banner = a_banner(site, "summer")

    site(CommandDeleteBanner(id=banner.id))

    assert site.history(banner.id) == ["EntityDeleted"]
    assert site.files_deleted == ["summer.png"]


def test_only_an_archivable_entity_has_a_trash(site: Site):
    with pytest.raises(exceptions.ProgrammingError, match="Note, which is not archivable"):
        site(CommandTrashNote(id="0192ffff"))


@dataclass
class Poster(ArchivableMixin, Entity):
    """Titled in two languages and shown by its caption: what a dry run names it by."""

    caption: str = ""

    @classmethod
    def translations(cls) -> dict[str, str]:
        return {"default": "Poster", "es": "Afiche"}

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:
        return "caption"


class CommandDeletePoster(Delete[Poster]):
    pass


class CommandTrashPoster(Archive[Poster]):
    pass


class CommandPurgePosters(Purge[Poster]):
    pass


def test_a_dry_run_names_the_entity_by_its_title_and_the_record_by_its_display_in_its_language():
    """A person reads «Se eliminará Afiche «Feria»», not a class name and an id; a screen that
    words it itself has the display in `data`."""
    bus = UseFramework("posters", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    poster = Poster(caption="Feria")
    old = Poster(caption="Viejo", archived_at=utc_now() - timedelta(days=45))
    repository.save([poster, old])

    @bus.feature([CommandDeletePoster, CommandTrashPoster, CommandPurgePosters])
    class PosterForms(EntityEdits[Poster]):
        pass

    def rehearsed(dto) -> Preview:
        preview = bus(dto)
        assert isinstance(preview, Preview)
        return preview

    in_english = rehearsed(CommandDeletePoster(id=poster.id, dry_run=True))
    with bus.context({"lang": "es"}):
        deleted = rehearsed(CommandDeletePoster(id=poster.id, dry_run=True))
        trashed = rehearsed(CommandTrashPoster(id=poster.id, dry_run=True))
        purged = rehearsed(CommandPurgePosters(older_than=timedelta(days=30), dry_run=True))

    assert in_english.text == "Would delete Poster «Feria»"
    assert deleted.text == "Se eliminará Afiche «Feria»"
    assert trashed.text == "Se archivará Afiche «Feria»"
    assert purged.text == "Se purgarán 1 Afiche archivados hace más de 30 días"
    assert deleted.data == {"id": poster.id, "version": 1, "display": "Feria"}
