"""The edit of one aggregate, written once: `Edit` keeps the caller's draft of a record, saves it
or discards it, and answers what the record is now — the whole life of a form in one command.

    class ResponseEditInvoice(ResponseEdit):
        invoice: Invoice

    class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]):
        pass

    @billing.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
    class InvoiceForms(EntityEdits[Invoice]):
        pass

    CommandEditInvoice(id="0192…", values={…})                               a draft, kept
    CommandEditInvoice(id="0192…", values={…}, version=N)                    kept again
    CommandEditInvoice(id="0192…", version=N, action=EditAction.SAVE)        stored
    CommandEditInvoice(id="0192…", version=N, action=EditAction.DISCARD)     dropped
    QueryGetInvoice(id="0192…", draft=True)                                  where it was left

A command of the project's own edits the same way: its DTO is an `Edit` with fields of its own,
its Feature an `EntityEdits` that changes the record with the domain's methods (`change`) — and
keeps, saves, discards and answers as the generic one does.

    class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
        product: str
        quantity: int

    @billing.feature(CommandAddInvoiceLine)
    class AddInvoiceLine(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandAddInvoiceLine, /) -> None:
            invoice.add_line(dto.product, dto.quantity)

Context: a draft is the caller's — whose, the context says (`EntityReads.drafts`) — and nothing is
stored until `SAVE`: a kept edit is computed over a copy inside `previewing()`, so a domain method
that writes is refused there, as in a preview. Each edit names the draft version it read; another
tab that kept first makes it lose with `DraftConflict`. A save with no draft is an edit too: the
values go straight to the store, refused when the record moved past `origin_version`.
"""

import dataclasses
from enum import StrEnum
from typing import Any

from pydantic import Field

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.drafts import Draft, DraftConflict
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.editing.editing import assign, form_values, recompute
from sincpro_framework.ddd.editing.preview import Advice, previewing
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.exceptions import StaleAggregate
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
    with_derived_relations,
)
from sincpro_framework.ddd.repositories.capabilities import Extends
from sincpro_framework.exceptions import ClientError, ProgrammingError
from sincpro_framework.sincpro_abstractions import DataTransferObject


class EditAction(StrEnum):
    KEEP = "keep"
    """The record as the form holds it, kept as the caller's draft: nothing is stored."""
    SAVE = "save"
    """The record stored, and the draft dropped."""
    DISCARD = "discard"
    """The draft dropped; the stored record, if any, stays as it was."""


class ResponseEdit(ResponseRecord):
    """What an edit answers: the record as it is now — its derived fields computed — each field's
    state over it and what the domain advised. `draft` names the version the next edit reads;
    `None` once the record is saved or the draft discarded.

    class ResponseEditInvoice(ResponseEdit):
        invoice: Invoice
    """

    fields: dict[str, FieldState] = {}
    advice: list[Advice] = []


class Edit[T, R: ResponseEdit](DataTransferObject):
    """A form's edit of one record: keep it as the caller's draft, save it, or discard it."""

    id: str = Field(default_factory=new_entity_id)
    """The record's key (`DEFAULT_GET_ID`): a stored record's, or the one a new record is created
    under — named by the caller (a UUID v7 a client mints), or minted here when it names none."""
    values: dict[str, Any] = {}
    """The record as the form holds it, whole, as JSON: each field read as its type."""
    changed: list[str] = []
    """The fields the form changed, so only the derivations they reach run; none runs them all."""
    version: int = 0
    """The draft version the caller read; `0` when it holds no draft."""
    origin_version: int | None = None
    """The stored record's version the caller read, for a save with no draft: refused when the
    record moved past it. A draft remembers its own."""
    action: EditAction = EditAction.KEEP


class EntityEdits[T](EntityReads[T]):
    """The Feature that answers `Edit` for one entity — and every read `EntityReads` answers, so
    one Feature can serve a form whole.

    @billing.feature([QueryGetInvoice, CommandEditInvoice])
    class InvoiceForms(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandEditInvoice, /) -> None:
            super().change(invoice, dto)        # the form's values, then the project's own
            ...
    """

    def change(self, record: Any, dto: Any, /) -> None:
        """What the edit does to the record: the form's values put on it, each read as its field's
        type. A command of the project's changes it with the domain's methods instead."""
        assign(record, dto.values)

    def answered(
        self,
        entity: type,
        response: type,
        record: Any,
        draft: Draft | None,
        stale: bool,
        advice: list[Advice],
    ) -> Any:
        """The record whole — what the form shows and sends back — with the model's definition,
        each field's state, the advice and the draft it is now."""
        answer = response(
            **{response.record_field(): record},
            entity_meta_data=self.described(entity, Criteria()).meta,
            fields=states_of(record),
            advice=list(advice),
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

    def keep(self, dto: Any) -> Any:
        """The edit kept as the caller's draft; nothing is stored.

        1. The working copy, inside `previewing()`: a copy of the stored record — or a new one
           under `id` — with the caller's draft on it, changed by this edit, its derivations
           recomputed; events it recorded are dropped, as a preview drops them.
        2. Its values kept as the draft, over the version the caller read (`DraftConflict` when
           another tab kept first); the draft remembers the stored version it started from.
        Final: the record, the new draft version, each field's state and the advice.
        """
        entity, response = declared_by(type(dto))
        drafts = self.drafts()
        subject = self.draft_subject(entity, dto.id)
        kept = drafts.read(subject)
        with previewing() as advice:
            stored = self.stored(entity, dto.id)
            record = self.shaped(
                entity, dto.id, stored, kept.values if kept is not None else {}, dto.values
            )
            if kept is not None:
                assign(record, kept.values, True)
            self.change(record, dto)
            recompute(record, dto.changed)
            pull = getattr(record, "pull_events", None)
            if callable(pull):
                pull()
        current = getattr(stored, "version", 0) if stored is not None else 0
        if kept is not None:
            origin = kept.origin_version
        elif dto.origin_version is not None:
            origin = dto.origin_version
        else:
            origin = current
        draft = drafts.keep(subject, form_values(entity, dumped(record)), origin, dto.version)
        return self.answered(
            entity, response, record, draft, stored is not None and current != origin, advice
        )

    def writable(self, entity: type, key: str) -> Any:
        """The stored record of `key` as the store holds it — the instance a save writes, read as
        a preview reads it, with the relations its derivations read — or `None` when there is
        none yet."""
        criteria = self.by_identity(
            entity, Condition(field=key_of(entity), value=key), 1, Criteria()
        )
        page = self.reads_from().search(entity, with_derived_relations(entity, criteria))
        return page.items[0] if page.items else None

    def save(self, dto: Any) -> Any:
        """The record stored and the caller's draft dropped.

        1. The version the caller read is the draft's — `0` when there is none — or the save loses
           with `DraftConflict`: another tab kept, saved or discarded first.
        2. The stored record is read as the store holds it — or a new one is made under `id` — and
           refused (`StaleAggregate`) when it moved past the version the edit started from: the
           draft's, else `origin_version` when the caller gave one.
        3. The draft's values, then this edit's change, on the record itself — the domain's
           methods record their events as on any write — and the record saved.
        Final: the draft dropped; the record as saved, with no draft.
        """
        entity, response = declared_by(type(dto))
        drafts = self.drafts()
        subject = self.draft_subject(entity, dto.id)
        kept = drafts.read(subject)
        if dto.version != (kept.version if kept is not None else 0):
            raise DraftConflict(subject, dto.version, kept.version if kept is not None else 0)
        record = self.writable(entity, dto.id)
        origin = kept.origin_version if kept is not None else dto.origin_version
        current = getattr(record, "version", 0) if record is not None else 0
        if record is not None and origin is not None and current != origin:
            raise StaleAggregate(
                f"{entity.__name__} {dto.id} changed since the edit started (version {origin}, "
                f"now {current}): read it again"
            )
        wanted = extensions.class_for(
            entity, set().union(kept.values if kept is not None else {}, dto.values)
        )
        if record is not None and not isinstance(record, wanted):
            repository = self.reads_from()
            if not isinstance(repository, Extends):
                raise ProgrammingError(
                    f"{type(repository).__name__} cannot give {entity.__name__} {dto.id} the "
                    f"rows of {wanted.__name__}: it keeps no extensions apart from the entity"
                )
            repository.extend(record, wanted)
            record = self.writable(entity, dto.id)
        elif record is None:
            record = self.shaped(
                entity, dto.id, None, kept.values if kept is not None else {}, dto.values
            )
        if kept is not None:
            assign(record, kept.values, True)
        self.change(record, dto)
        self.reads_from().save(record)
        if kept is not None:
            drafts.discard(subject)
        return self.answered(entity, response, record, None, False, [])

    def discard(self, dto: Any) -> Any:
        """The caller's draft dropped; the record as stored — or a new one under `id` — answered
        with no draft."""
        entity, response = declared_by(type(dto))
        self.drafts().discard(self.draft_subject(entity, dto.id))
        stored = self.stored(entity, dto.id)
        record = stored if stored is not None else keyed(blank(entity), entity, dto.id)
        return self.answered(entity, response, record, None, False, [])

    def nested_names(self, entity: type, changed: list[str]) -> list[str]:
        """The fields a form reports as changed, as flat names: `delivery` is every field of the
        extension, `delivery.route` is `route`."""
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

            {"number": "F-1", "delivery": {"route": "R7"}, "tracking": None}
              →  {"number": "F-1", "route": "R7"}

        An entity whose answers nest each extension under its key (`delegated=[Move]`) is read back
        by a form in that shape, and sends it back as it read it. The nested values are the fields
        of that extension; `None` is a record that has no such extension, and writes nothing. Flat
        values are taken as they are, whatever the entity delegates, and a key that is not an
        extension of it is left for `assign` to refuse as any unknown field.
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
                raise ClientError(
                    f"{one.key} has no field {', '.join(unknown)}: it has {', '.join(one.fields)}"
                )
            flat.update(nested)
        return flat

    def edit(self, dto: Any) -> Any:
        """Each action is a method — `keep`, `save`, `discard` — to override and call `super()`.
        The values arrive flat, whichever shape the form read the record in."""
        entity, _ = declared_by(type(dto))
        flat = self.flattened(entity, dto.values)
        if flat != dto.values or self.nested_names(entity, dto.changed):
            dto = dto.model_copy(
                update={"values": flat, "changed": self.nested_names(entity, dto.changed)}
            )
        match dto.action:
            case EditAction.SAVE:
                return self.save(dto)
            case EditAction.DISCARD:
                return self.discard(dto)
        return self.keep(dto)

    def execute(self, dto: Any) -> Any:
        if isinstance(dto, Edit):
            return self.edit(dto)
        return super().execute(dto)
