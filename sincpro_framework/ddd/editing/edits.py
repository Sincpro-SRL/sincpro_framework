"""The edit of one aggregate, written once: `Edit` saves the caller's draft of a record,
stores it or discards it, and answers what the record is now — the whole life of a form in one
command —, and `Delete` deletes it.

    class ResponseEditInvoice(ResponseEdit):
        invoice: Invoice

    class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]):
        pass

    class CommandDeleteInvoice(Delete[Invoice]):
        pass

    @billing.feature([QueryGetInvoice, CommandEditInvoice, CommandDeleteInvoice])
    class InvoiceForms(EntityEdits[Invoice]):
        pass

    CommandEditInvoice(id="0192…", values={…})                               a draft, saved
    CommandEditInvoice(id="0192…", values={…}, version=N)                    saved again
    CommandEditInvoice(id="0192…", version=N, action=EditAction.SAVE)        stored
    CommandEditInvoice(id="0192…", version=N, action=EditAction.DISCARD)     dropped
    QueryGetInvoice(id="0192…", draft=True)                                  where it was left
    CommandDeleteInvoice(id="0192…", dry_run=True)                           what would go
    CommandDeleteInvoice(id="0192…")                                         deleted

A record that goes to a trash first is `ArchivableMixin`: `Archive` puts it away, `Restore`
brings it back, and a `Purge` a cron of the project runs deletes what was archived long enough.

A command of the project's own edits the same way: its DTO is an `Edit` with fields of its own,
its Feature an `EntityEdits` that changes the record with the domain's methods (`change`) — and
saves drafts, stores, discards and answers as the generic one does.

    class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
        product: str
        quantity: int

    @billing.feature(CommandAddInvoiceLine)
    class AddInvoiceLine(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandAddInvoiceLine, /) -> None:
            invoice.add_line(dto.product, dto.quantity)

Context: a draft is the caller's — whose, the context says (`EntityReads.drafts`) — and nothing is
stored until `SAVE`: an edit saved as a draft is computed over a copy inside `read_only()`,
so a domain method that writes is refused there, as in a preview. Each edit names the draft
version it read; another tab that saved its draft first makes it lose with `DraftConflict`. A
save with no draft is an edit too: the values go straight to the store, refused when the
record moved past `origin_version`. A deletion drops the caller's draft of the record; anyone
else's is stale from then on, and saving it is refused.
"""

import dataclasses
from contextlib import nullcontext
from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context import Draft
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pagination,
    Sort,
)
from sincpro_framework.ddd.editing.editing import apply_values, form_values, recompute
from sincpro_framework.ddd.editing.preview import FormWarning, read_only
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.entity import utc_now
from sincpro_framework.ddd.entity.mixins.archivable import ArchivableMixin
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.ddd.query import DraftState, ResponseRecord
from sincpro_framework.ddd.reads.reads import (
    EntityReads,
    FieldState,
    blank,
    declared_by,
    dumped,
    key_of,
    keyed,
    states_of,
    with_computed_relations,
)
from sincpro_framework.ddd.repositories.capabilities import Extends, Transacts
from sincpro_framework.sincpro_abstractions import (
    DataTransferObject,
    DryRunnable,
    Preview,
)
from sincpro_framework.sincpro_logger import logger


class EditAction(StrEnum):
    """What an `Edit` does with the record the form holds — save it as a draft, store it, or
    drop it."""

    SAVE_DRAFT = "save_draft"
    """The record as the form holds it, saved as the caller's draft: nothing is stored."""
    SAVE = "save"
    """The record stored, and the draft dropped."""
    DISCARD = "discard"
    """The draft dropped; the stored record, if any, stays as it was."""


class ResponseEdit(ResponseRecord):
    """What an edit answers: the record as it is now — its computed fields up to date — each field's
    state over it and what the domain warned the form about. `draft` names the version the next edit reads;
    `None` once the record is saved or the draft discarded.

    class ResponseEditInvoice(ResponseEdit):
        invoice: Invoice
    """

    states: dict[str, FieldState] = {}
    warnings: list[FormWarning] = []


class Edit[T, R: ResponseEdit](DataTransferObject):
    """A form's edit of one record: save it as the caller's draft, store it, or discard it."""

    id: str = Field(default_factory=new_entity_id)
    """The record's key (`DEFAULT_GET_ID`): a stored record's, or the one a new record is created
    under — named by the caller (a UUID v7 a client mints), or minted here when it names none."""
    values: dict[str, Any] = {}
    """The record as the form holds it, whole, as JSON: each field read as its type. What the
    framework writes comes back as the form read it and is left out (`assignable`)."""
    changed: list[str] = []
    """The fields the form changed, so only the computations they reach run; none runs them all."""
    version: int = 0
    """The draft version the caller read; `0` when it holds no draft."""
    origin_version: int | None = None
    """The stored record's version the caller read, for a save with no draft: refused when the
    record moved past it, or was deleted. The record's own `version`, sent back in `values`,
    says it when this does not. A draft remembers its own."""
    action: EditAction = EditAction.SAVE_DRAFT


class Delete[T](DryRunnable):
    """A record deleted by its key — `dry_run=True` says what would go, and deletes nothing.

        class CommandDeleteInvoice(Delete[Invoice]):
            pass

    It answers nothing, and every wire says so: a `204` over REST, `null` in the contract.
    """

    id: str
    """The record's key (`DEFAULT_GET_ID`)."""
    origin_version: int | None = None
    """The stored record's version the caller read: refused when the record moved past it."""


class Archive[T](DryRunnable):
    """A record put away by its key — out of every reading, never lost: a trash a `Restore`
    brings it back from and a `Purge` empties. `dry_run=True` says what would be archived.

        class CommandTrashBanner(Archive[Banner]):      # Banner(ArchivableMixin, Entity)
            pass

    It answers nothing, as `Delete` does.
    """

    id: str
    """The record's key (`DEFAULT_GET_ID`)."""
    origin_version: int | None = None
    """The stored record's version the caller read: refused when the record moved past it."""


class Restore[T](DryRunnable):
    """An archived record brought back by its key, into every reading again. `dry_run=True` says
    what would be restored.

        class CommandRestoreBanner(Restore[Banner]):
            pass
    """

    id: str
    """The archived record's key (`DEFAULT_GET_ID`)."""
    origin_version: int | None = None
    """The archived record's version the caller read: refused when it moved past it."""


class Purge[T](DryRunnable):
    """The records archived longer ago than `older_than`, deleted for good — what a cron of the
    project runs when it wants its trash emptied; the framework never schedules it.

        class CommandPurgeBanners(Purge[Banner]):
            pass

        CommandPurgeBanners(older_than=timedelta(days=30))                 deleted, batch by batch
        CommandPurgeBanners(older_than=timedelta(days=30), dry_run=True)   how many would go
    """

    older_than: timedelta = Field(ge=timedelta(0))
    """How long a record stays archived before it is deleted for good."""
    batch_size: int = Field(default=500, gt=0)
    """How many records each unit of work deletes."""


def _archivable(entity: type, dto: Any) -> None:
    """Refuses an `Archive`, a `Restore` or a `Purge` of an entity that has nowhere to say it
    was archived."""
    if not issubclass(entity, ArchivableMixin):
        raise exceptions.ProgrammingError(
            f"{type(dto).__name__} archives {entity.__name__}, which is not archivable: "
            f"class {entity.__name__}(ArchivableMixin, Entity)"
        )


class EntityEdits[T](EntityReads[T]):
    """The Feature that answers `Edit`, `Delete`, `Archive`, `Restore` and `Purge` for one
    entity — and every read `EntityReads` answers, so one Feature can serve a form whole.

    @billing.feature([QueryGetInvoice, CommandEditInvoice])
    class InvoiceForms(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandEditInvoice, /) -> None:
            super().change(invoice, dto)        # the form's values, then the project's own
            ...
    """

    def change(self, record: Any, dto: Any, /) -> None:
        """What the edit does to the record: the form's values put on it, each read as its field's
        type, a many-to-many named by ids as the records they name (`referenced`). A command of
        the project's changes it with the domain's methods instead."""
        apply_values(record, self.referenced(type(record), dto.values))

    def answered(
        self,
        entity: type,
        response: type,
        record: Any,
        draft: Draft | None,
        stale: bool,
        warnings: list[FormWarning],
    ) -> Any:
        """The record whole — what the form shows and sends back — with the version of its
        model, each field's state, the warnings and the draft it is now."""
        answer = response(
            **{response.record_field(): record},
            model_version=model_of(entity).fingerprint,
            states=states_of(record),
            warnings=list(warnings),
        )
        if draft is not None:
            answer.draft = DraftState(
                version=draft.version,
                origin_version=draft.origin_version,
                kept_at=draft.kept_at,
                stale=stale,
            )
        return answer

    def shaped(self, entity: type, key: str, record: Any, *values: dict[str, Any]) -> Any:
        """The record as the class its values need.

            values with a field of an extension (`route`)    →  the extension's class
            a new record                                      →  built as that class, under `key`
            a stored record of another class                  →  a copy of it as that class

        A form of the entity sends the fields of every extension installed, and the extension's
        class is what holds them: `Move` has no field `route`, `MoveDelivery` does. Fields that
        belong to siblings no one class holds are refused, naming them.
        """
        needed = extensions.class_for(entity, set().union(*values))
        if record is None:
            return keyed(blank(needed), entity, key)
        if isinstance(record, needed):
            return record
        promoted = blank(needed)
        for field in dataclasses.fields(record):
            setattr(promoted, field.name, getattr(record, field.name))
        return promoted

    def save_draft(self, dto: Any) -> Any:
        """The edit saved as the caller's draft; nothing is stored.

            # invoice 0192… stored at version 3, no draft yet
            save_draft(CommandEditInvoice(id="0192…", values={"discount": "5"},
                                          changed=["discount"]))
                ->  ResponseEditInvoice(invoice=Invoice(discount=5, total=95, …),
                        draft=DraftState(version=1, origin_version=3, stale=False), …)
            # the same edit again with version=0, another tab having saved its draft first
                ->  raises DraftConflict

        1. The working copy, inside `read_only()`: a copy of the stored record — or a new one
           under `id` — with the caller's draft on it, changed by this edit, its computations
           recomputed; events it recorded are dropped, as a preview drops them.
        2. Its values saved as the draft, over the version the caller read (`DraftConflict`
           when another tab saved first); the draft remembers the stored version it started
           from.
        Final: the record, the new draft version, each field's state and the warnings.
        """
        entity, response = declared_by(type(dto))
        drafts = self.drafts()
        subject = self.draft_subject(entity, dto.id)
        existing = drafts.read(subject)
        with read_only() as warnings:
            stored = self.stored(entity, dto.id)
            record = self.shaped(
                entity,
                dto.id,
                stored,
                existing.values if existing is not None else {},
                dto.values,
            )
            if existing is not None:
                apply_values(record, self.referenced(type(record), existing.values))
            self.change(record, dto)
            recompute(record, dto.changed)
            pull = getattr(record, "pull_events", None)
            if callable(pull):
                pull()
        current = getattr(stored, "version", 0)
        if existing is not None:
            origin = existing.origin_version
        elif dto.origin_version is not None:
            origin = dto.origin_version
        else:
            origin = current
        draft = drafts.save(subject, form_values(entity, dumped(record)), origin, dto.version)
        return self.answered(entity, response, record, draft, current != origin, warnings)

    def writable(self, entity: type, key: str, archived: bool = False) -> Any:
        """The stored record of `key` as the store holds it — the instance a save or a deletion
        writes, read as a preview reads it, with the relations its computations read — or
        `None` when there is none yet. `archived` reads an archived one, which no other
        reading answers."""
        where: Any = Condition(field=key_of(entity), value=key)
        if archived:
            where = All(
                all=[
                    where,
                    Condition(field="archived_at", operator=Operator.IS_NULL, value=False),
                ]
            )
        criteria = self.by_identity(entity, where, 1, Criteria())
        page = self.reads_from().search(entity, with_computed_relations(entity, criteria))
        return page.items[0] if page.items else None

    def save(self, dto: Any) -> Any:
        """The record stored and the caller's draft dropped.

        1. The version the caller read is the draft's — `0` when there is none — or the save loses
           with `DraftConflict`: another tab saved a draft, stored or discarded first.
        2. The stored record is read as the store holds it — or a new one is made under `id` — and
           refused (`StaleAggregate`) when it moved past the version the edit started from, or
           was deleted since: the draft's, else `origin_version` when the caller gave one.
           2.1 A stored record whose values need an extension's class it does not have yet is
               given that extension's rows (`Extends`), then read again.
        3. The draft's values, then this edit's change, on the record itself — the domain's
           methods record their events as on any write — and the record saved.
        Final: the draft dropped; the record as saved, with no draft.
        """
        entity, response = declared_by(type(dto))
        drafts = self.drafts()
        subject = self.draft_subject(entity, dto.id)
        existing = drafts.read(subject)
        if dto.version != (existing.version if existing is not None else 0):
            raise exceptions.DraftConflict(
                subject, dto.version, existing.version if existing is not None else 0
            )
        record = self.writable(entity, dto.id)
        origin = existing.origin_version if existing is not None else dto.origin_version
        current = getattr(record, "version", 0)
        if origin is not None and current != origin:
            now = f"now {current}" if record is not None else "deleted or archived since"
            raise exceptions.StaleAggregate(
                f"{entity.__name__} {dto.id} changed since the edit started (version {origin}, "
                f"{now}): read it again"
            )
        wanted = extensions.class_for(
            entity, set().union(existing.values if existing is not None else {}, dto.values)
        )
        if record is not None and not isinstance(record, wanted):
            repository = self.reads_from()
            if not isinstance(repository, Extends):
                raise exceptions.ProgrammingError(
                    f"{type(repository).__name__} cannot give {entity.__name__} {dto.id} the "
                    f"rows of {wanted.__name__}: it holds no extensions apart from the entity"
                )
            repository.extend(record, wanted)
            record = self.writable(entity, dto.id)
        elif record is None:
            record = self.shaped(
                entity,
                dto.id,
                None,
                existing.values if existing is not None else {},
                dto.values,
            )
        if existing is not None:
            apply_values(record, self.referenced(type(record), existing.values))
        self.change(record, dto)
        self.reads_from().save(record)
        if existing is not None:
            drafts.discard(subject)
        return self.answered(entity, response, record, None, False, [])

    def discard(self, dto: Any) -> Any:
        """The caller's draft dropped; the record as stored — or a new one under `id` — answered
        with no draft.

            discard(CommandEditInvoice(id="0192…", version=2, action=EditAction.DISCARD))
                ->  ResponseEditInvoice(invoice=<as stored>, draft=None, warnings=[], …)
            # no stored invoice under the id  ->  a blank Invoice keyed "0192…", draft=None

        Context: the version the caller read is not checked — dropping a draft another tab
        saved since is still dropping it.
        """
        entity, response = declared_by(type(dto))
        self.drafts().discard(self.draft_subject(entity, dto.id))
        stored = self.stored(entity, dto.id)
        record = stored if stored is not None else keyed(blank(entity), entity, dto.id)
        return self.answered(entity, response, record, None, False, [])

    def nested_names(self, entity: type, changed: list[str]) -> list[str]:
        """The fields a form reports as changed, as flat names: `delivery` is every field of the
        extension, `delivery.route` is `route`.

            # Move delegates the extension `delivery` (fields route, carrier)
            nested_names(Move, ["number", "delivery.route"])   ->  ["number", "route"]
            nested_names(Move, ["delivery"])                   ->  ["route", "carrier"]
            nested_names(Invoice, ["delivery.route"])          ->  ["delivery.route"]
            # Invoice delegates no extension: its names come back as they are

        A name whose prefix is no extension's key is returned as it came.
        """
        if entity not in extensions.DELEGATED:
            return changed
        found: list[str] = []
        for name in changed:
            key, _, field = name.partition(".")
            extension = next(
                (one for one in extensions.EXTENSIONS.get(entity, []) if one.key == key), None
            )
            if extension is None:
                found.append(name)
            else:
                found.extend([field] if field else extension.fields)
        return found

    def flattened(self, entity: type, values: dict[str, Any]) -> dict[str, Any]:
        """The values of a form as the flat fields of one record.

            flattened(Move, {"number": "F-1", "delivery": {"route": "R7"}, "tracking": None})
                ->  {"number": "F-1", "route": "R7"}
            flattened(Move, {"delivery": {"colour": "red"}})
                ->  raises ClientError("delivery has no field colour: it has route, carrier")

        An entity whose answers nest each extension under its key (`delegated=[Move]`) is read back
        by a form in that shape, and sends it back as it read it. The nested values are the fields
        of that extension; `None` is a record that has no such extension, and writes nothing. Flat
        values are taken as they are, whatever the entity delegates, and a key that is not an
        extension of it is left for `apply_values` to refuse as any unknown field.

        1. An entity that delegates no extension: the values as they came.
        2. For each extension of the entity, its key among the values:
           2.1 taken out of the values;
           2.2 `None` writes nothing;
           2.3 a field the extension does not have is a `ClientError`, naming it;
           2.4 else its fields are put among the flat values.
        Final: one record's flat fields — the caller's dict left untouched.
        """
        if entity not in extensions.DELEGATED:
            return values
        flat = dict(values)
        for one in extensions.EXTENSIONS.get(entity, []):
            if one.key not in flat:
                continue
            nested = flat.pop(one.key)
            if nested is None:
                continue
            unknown = sorted(set(nested) - set(one.fields))
            if unknown:
                raise exceptions.ClientError(
                    f"{one.key} has no field {', '.join(unknown)}: it has {', '.join(one.fields)}"
                )
            flat.update(nested)
        return flat

    def edit(self, dto: Any) -> Any:
        """Run the edit's action — each one a method (`save_draft`, `save`, `discard`) to
        override and call `super()`.

            edit(CommandEditInvoice(id="0192…", values={"discount": "5"}))
                ->  save_draft's answer — a draft saved, nothing stored
            edit(CommandEditInvoice(id="0192…", version=1, action=EditAction.SAVE))
                ->  save's answer — stored, draft=None
            edit(CommandEditMove(values={"delivery": {"route": "R7"}}, changed=["delivery"]))
                ->  save_draft of values={"route": "R7"}, changed=["route", "carrier"]
            edit(CommandEditInvoice(id="0192…", values={"id": "0192…", "version": 3, …},
                                    action=EditAction.SAVE))
                ->  save's answer, refused when the stored invoice moved past version 3

        1. The values and the changed names flattened: whichever shape the form read the
           record in — extensions nested under their key or flat — they arrive as one record's
           fields.
        2. The record's echo left out of the values (`assignable`); the `version` it carried
           is the stored version the edit started from, when the caller named none.
        Final: `SAVE` stores, `DISCARD` drops, anything else saves the draft.
        """
        entity, _ = declared_by(type(dto))
        values = self.flattened(entity, dto.values)
        echoed = (
            values.get("version") if "version" in model_of(entity).framework_fields else None
        )
        dto = dto.model_copy(
            update={
                "values": self.assignable(entity, dto.id, values),
                "changed": self.nested_names(entity, dto.changed),
                "origin_version": (
                    echoed
                    if dto.origin_version is None and isinstance(echoed, int)
                    else dto.origin_version
                ),
            }
        )
        match dto.action:
            case EditAction.SAVE:
                return self.save(dto)
            case EditAction.DISCARD:
                return self.discard(dto)
        return self.save_draft(dto)

    def deleting(self, record: Any, dto: Any, /) -> None:
        """What the deletion does with the record before it goes: nothing, by default. Override
        it to refuse when the domain says the record cannot go, to record the fact it leaves —
        stored with the removal, so the record's history holds it after the row is gone — and
        to wait for the commit for what lives outside the store:

            def deleting(self, banner: Banner, dto: CommandDeleteBanner, /) -> None:
                banner.record(BannerDeleted())
                self.repository.after_commit(lambda: self.files.delete(banner.picture))

        Context: it runs only when the record is deleted, never in a dry run — what it waits
        for needs a commit, and a dry run has none.
        """

    def archiving(self, record: Any, dto: Any, /) -> None:
        """What archiving does with the record before it is put away: nothing, by default.
        Override it to refuse when the domain says the record cannot go to the trash."""

    def restoring(self, record: Any, dto: Any, /) -> None:
        """What restoring does with the record before it comes back: nothing, by default.
        Override it to refuse when it cannot come back — a code another record took
        meanwhile."""

    def targeted(self, entity: type, dto: Any, archived: bool = False) -> Any:
        """The stored record a `Delete`, an `Archive` or a `Restore` names, as the store holds
        it — the instance it writes; an archived one for a `Restore`.

        1. `AggregateNotFound` when there is none.
        2. `StaleAggregate` when it moved past the version the caller read.
        Final: the record.
        """
        record = self.writable(entity, dto.id, archived)
        if record is None:
            missing = "is not archived" if archived else "does not exist"
            raise exceptions.AggregateNotFound(f"{entity.__name__} {dto.id} {missing}")
        current = getattr(record, "version", 0)
        if dto.origin_version is not None and current != dto.origin_version:
            raise exceptions.StaleAggregate(
                f"{entity.__name__} {dto.id} changed since it was read (version "
                f"{dto.origin_version}, now {current}): read it again"
            )
        return record

    def purgeable(self, entity: type, dto: Any) -> Criteria:
        """What a `Purge` deletes, a batch at a time: the records archived before `older_than`
        ago, oldest identity first. Override it to keep some longer."""
        return Criteria(
            selection=Condition(
                field="archived_at", operator=Operator.LT, value=utc_now() - dto.older_than
            ),
            order=Sort.parse("id"),
            pagination=Pagination(limit=dto.batch_size),
            count=CountMode.NONE,
        )

    def delete(self, dto: Any) -> None:
        """The record deleted, and the caller's draft of it dropped.

            delete(CommandDeleteBanner(id="0192…"))                     ->  None; the banner gone
            delete(CommandDeleteBanner(id="0192…", origin_version=2))   # stored at version 3
                ->  raises StaleAggregate
            delete(CommandDeleteBanner(id="0193…"))     ->  raises AggregateNotFound

        1. In one unit of work when the store transacts, so what `deleting` waits for runs once
           the deletion committed, and none of it when it is undone.
        2. The stored record (`targeted`), `deleting`, then the record removed: what it
           recorded is stored with the removal, and the parts it owns go with it.
        Final: the caller's draft of it dropped, once the deletion is stored.
        """
        entity, _ = declared_by(type(dto))
        repository = self.reads_from()
        with repository.context() if isinstance(repository, Transacts) else nullcontext():
            record = self.targeted(entity, dto)
            self.deleting(record, dto)
            repository.remove(record)
        self.drafts().discard(self.draft_subject(entity, dto.id))

    def archive(self, dto: Any) -> None:
        """The record put away — out of every reading, kept for a `Restore` — and the caller's
        draft of it dropped.

            archive(CommandTrashBanner(id="0192…"))     ->  None; restorable until purged

        1. Only an archivable entity.
        2. In one unit of work when the store transacts, as a deletion is.
        3. The stored record (`targeted`), `archiving`, then the record archived.
        Final: the caller's draft of it dropped.
        """
        entity, _ = declared_by(type(dto))
        _archivable(entity, dto)
        repository = self.reads_from()
        with repository.context() if isinstance(repository, Transacts) else nullcontext():
            record = self.targeted(entity, dto)
            self.archiving(record, dto)
            repository.archive(record)
        self.drafts().discard(self.draft_subject(entity, dto.id))

    def restore(self, dto: Any) -> None:
        """An archived record brought back into every reading.

            restore(CommandRestoreBanner(id="0192…"))   ->  None; the banner read again
            restore(CommandRestoreBanner(id="0193…"))   ->  raises AggregateNotFound, none archived

        1. Only an archivable entity.
        2. In one unit of work when the store transacts.
        Final: the archived record (`targeted`), `restoring`, then the record restored and saved.
        """
        entity, _ = declared_by(type(dto))
        _archivable(entity, dto)
        repository = self.reads_from()
        with repository.context() if isinstance(repository, Transacts) else nullcontext():
            record = self.targeted(entity, dto, archived=True)
            self.restoring(record, dto)
            record.restore()
            repository.save(record)

    def purge(self, dto: Any) -> None:
        """Every record archived longer ago than `older_than`, deleted for good.

            purge(CommandPurgeBanners(older_than=timedelta(days=30)))   ->  None; logged: how many

        1. Only an archivable entity.
        2. A batch at a time (`purgeable`), each one unit of work when the store transacts:
           `deleting` for each record, then the batch removed — their facts stored with them.
        3. Until a batch finds none. A refusal stops the purge; the batches before it stay
           deleted.
        Final: how many went, logged once.
        """
        entity, _ = declared_by(type(dto))
        _archivable(entity, dto)
        repository = self.reads_from()
        purged = 0
        while True:
            with repository.context() if isinstance(repository, Transacts) else nullcontext():
                batch = list(repository.search(entity, self.purgeable(entity, dto)).items)
                for one in batch:
                    self.deleting(one, dto)
                repository.remove(batch)
            if not batch:
                break
            purged += len(batch)
        logger.info(
            f"purged {purged} {entity.__name__} archived more than {dto.older_than} ago"
        )

    def would(self, dto: Any, verb: str, archived: bool = False) -> Preview:
        """What a dry run of an action on one record answers: the record found and checked as
        the action checks it (`targeted`), named as a person reads it.

            would(CommandDeleteBanner(id="0192…", dry_run=True), "delete")
                ->  Preview(text="Would delete Banner 0192… «Summer sale»",
                            data={"id": "0192…", "version": 3})

        The version in `data` is the one a confirmed action names as `origin_version`, so it
        writes the record the person was shown.
        """
        entity, _ = declared_by(type(dto))
        record = self.targeted(entity, dto, archived)
        display = model_of(entity).presented.display
        shown = f" «{getattr(record, display)}»" if display else ""
        return Preview(
            text=f"Would {verb} {entity.__name__} {dto.id}{shown}",
            data={"id": dto.id, "version": getattr(record, "version", 0)},
        )

    def preview(self, dto: Any) -> Any:
        """A dry run of a `Delete`, an `Archive`, a `Restore` or a `Purge`: checked as it would
        be, nothing written and no hook run. Any other DTO is the `PreviewEntity`
        `EntityReads` answers.

            preview(CommandPurgeBanners(older_than=timedelta(days=30), dry_run=True))
                ->  Preview(text="Would purge 12 Banner archived more than 30 days, 0:00:00 ago",
                            data={"count": 12})
        """
        match dto:
            case Delete():
                return self.would(dto, "delete")
            case Archive():
                _archivable(declared_by(type(dto))[0], dto)
                return self.would(dto, "archive")
            case Restore():
                _archivable(declared_by(type(dto))[0], dto)
                return self.would(dto, "restore", archived=True)
            case Purge():
                entity, _ = declared_by(type(dto))
                _archivable(entity, dto)
                selection = self.purgeable(entity, dto).selection
                count = self.reads_from().count(entity, Criteria(selection=selection)).value
                return Preview(
                    text=f"Would purge {count} {entity.__name__} archived more than "
                    f"{dto.older_than} ago",
                    data={"count": count},
                )
        return super().preview(dto)

    def execute(self, dto: Any) -> Any:
        """An `Edit` is edited, a `Delete` deleted, an `Archive` archived, a `Restore` restored
        and a `Purge` purged; every other DTO is a read `EntityReads` answers."""
        match dto:
            case Edit():
                return self.edit(dto)
            case Delete():
                return self.delete(dto)
            case Archive():
                return self.archive(dto)
            case Restore():
                return self.restore(dto)
            case Purge():
                return self.purge(dto)
        return super().execute(dto)
