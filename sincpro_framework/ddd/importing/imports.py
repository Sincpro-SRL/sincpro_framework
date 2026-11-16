"""The import of one entity's records from a file, written once: `Import` reads CSV, JSON or an
Excel sheet and saves each record as a save does — its rules judged, its children with it, each
lookup to another record turned into that record's identity.

    class CommandImportPartners(Import[Partner, ResponseImport]):
        pass

    @sales.feature(CommandImportPartners)
    class PartnerImports(EntityImports[Partner]):
        pass

    sales(CommandImportPartners(content=data, format=ImportFormat.CSV), ResponseImport)
        ->  ResponseImport(created=1200, updated=30, unchanged=4, skipped=[])

Context: a file is read once, a batch at a time, inside one unit of work — the size of the file
decides how long it takes, never how much memory it holds. Five steps, each a method to override:
`rows` reads the file, `records` reads each row as a record of the entity, `resolve` turns every
key into an identity — the record's own, to find the one it updates, and each lookup —,
`build` puts the values on the record and judges it, `write` saves the batch. A key is looked up
once per import, with one search per entity and kind of key per batch, never one per row.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import batched
from typing import Any, ClassVar

from pydantic import ConfigDict, Field

from sincpro_framework.common.failures.problem import FieldError, pointer
from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pagination,
    Specification,
)
from sincpro_framework.ddd.editing.editing import adapter_of, apply_values, recompute_whole
from sincpro_framework.ddd.editing.preview import read_only
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.entity_collection import (
    EntityCollection,
    identity_name,
    identity_of,
)
from sincpro_framework.ddd.importing.external_key import ExternalKey
from sincpro_framework.ddd.importing.reading import ImportFormat, ImportRow, rows_of
from sincpro_framework.ddd.importing.shaping import (
    KEY,
    Holds,
    Lookup,
    is_blank,
    lookups_in,
    shaped,
    slots_of,
    unknown_names,
)
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.ddd.reads.reads import (
    EntityReads,
    blank,
    declared_by,
    key_of,
    with_computed_relations,
)
from sincpro_framework.ddd.repositories.capabilities import Transacts
from sincpro_framework.sincpro_abstractions import DataTransferObject, DryRunnable, Preview
from sincpro_framework.sincpro_logger import logger

FLAT = (ImportFormat.CSV, ImportFormat.EXCEL)
"""The formats that write a record's children on the rows below it."""


class ImportMode(StrEnum):
    """What an import does with a record the file names: create it, update it, or either."""

    CREATE_OR_UPDATE = "create_or_update"
    """A record found by its key is updated, any other is created."""
    CREATE = "create"
    """Every record is new: one found by its key is refused."""
    UPDATE = "update"
    """Every record exists: one that is not found is refused."""


class ImportOutcome(StrEnum):
    """What the import did with one record."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    """Found, and the file holds nothing it does not already hold: not written."""


class ResponseImport(DataTransferObject):
    """What an import did: how many records it created, updated and left as they were, and the
    records it left out under `skip_invalid=True`, each pointing at its cell."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[FieldError] = []


class Import[T, R: ResponseImport](DryRunnable):
    """The records of one entity, from a file: `dry_run=True` reads, resolves and judges them
    all, and writes nothing."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    content: bytes
    """The file. As JSON — over HTTP, JSON-RPC, MCP — it travels in base64."""
    format: ImportFormat
    sheet: str = ""
    """The sheet of a workbook; its first sheet when blank."""
    origin: str = "import"
    """Who named the records' external keys (`@key`): `"odoo"`, `"legacy_excel"`."""
    mode: ImportMode = ImportMode.CREATE_OR_UPDATE
    skip_invalid: bool = False
    """`False`: one invalid record refuses the whole file, naming every invalid record. `True`:
    an invalid record is left out and named in the answer, the rest is written."""
    columns: dict[str, str | None] = {}
    """A header renamed to a field, or dropped with `None`: `{"Razón social": "name"}`."""
    decimal: str = "."
    """The decimal separator of the numbers written as text; with `","` the point separates
    thousands."""
    encoding: str = "utf-8-sig"
    """The encoding of a CSV or a JSON file."""
    batch_size: int = Field(default=500, gt=0)
    """How many records are read, resolved and saved at a time."""


@dataclass
class ImportRecord:
    """One record of the file on its way to the store: where it is, its values, how the file
    names it, and what became of it."""

    at: tuple[str | int, ...]
    values: dict[str, Any] = field(default_factory=dict)
    """As `apply_values` takes them, each record they name a `Lookup` until it is resolved."""
    keys: list[tuple[str, str]] = field(default_factory=list)
    """How the file names the record, in the order it is found by: its external key (`@key`),
    its identity, its natural key."""
    identity: str = ""
    """Its identity, once resolved: the stored record's, the one the file gives, or a new one."""
    new_key: bool = False
    """It has an external key the store does not hold yet: written with it."""
    stored: Any = None
    """The stored record it updates; `None` for a new one."""
    record: Any = None
    """The aggregate to save, once built."""
    outcome: ImportOutcome | None = None
    failure: exceptions.InvalidValue | None = None


def whole_records(
    entity: type, rows: Iterable[ImportRow], flat: bool
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Each record of the file with its values, its children gathered from the rows below it.

        # number,lines/product_id,lines/quantity  —  F-1,SKU-7,2  ·  ,SKU-9,1
        ->  ((2,), {"number": "F-1", "lines": [{"product_id": "SKU-7", "quantity": "2"},
                                                {"product_id": "SKU-9", "quantity": "1"}]})

    1. The names of the first row are checked against the entity: a header that names no field
       refuses the whole file, before any record is read.
    2. A flat row whose own cells are all blank continues the record above it: the children it
       holds are added to that record's.
    Final: a JSON record comes as it is, nested already.
    """
    if not flat:
        for row in rows:
            yield row.at, row.cells
        return
    children = [
        name for name, slot in slots_of(entity).items() if slot.holds == Holds.RECORDS
    ]
    held: tuple[tuple[str | int, ...], dict[str, Any]] | None = None
    for row in rows:
        if held is None:
            unknown = unknown_names(entity, row.cells)
            if unknown:
                raise exceptions.ImportRefused(
                    tuple(
                        exceptions.InvalidValue(
                            f"{entity.__name__} has no field {name}",
                            ("header", name),
                            "content",
                        )
                        for name in unknown
                    )
                )
        values = dict(row.cells)
        owned = {
            name: values.pop(name)
            for name in children
            if isinstance(values.get(name), dict) and set(values[name]) != {KEY}
        }
        if held is not None and is_blank(values):
            for name, child in owned.items():
                if not is_blank(child):
                    held[1].setdefault(name, []).append(child)
            continue
        if held is not None:
            yield held
        values.update(
            {name: [] if is_blank(child) else [child] for name, child in owned.items()}
        )
        held = (row.at, values)
    if held is not None:
        yield held


def _positions(record: Any, path: tuple[str | int, ...]) -> tuple[str | int, ...]:
    """A path into the record with each child named by its position, as the file holds it,
    instead of by its identity, as a check names it: `("lines", "0192…", "qty")` → `("lines", 1,
    "qty")`."""
    found: list[str | int] = []
    value = record
    for part in path:
        items = list(value) if isinstance(value, (list, EntityCollection)) else None
        if items is not None and isinstance(part, str):
            part = next(
                (at for at, one in enumerate(items) if str(identity_of(one)) == part), part
            )
        found.append(part)
        if items is not None and isinstance(part, int):
            value = items[part] if part < len(items) else None
        else:
            value = getattr(value, str(part), None)
    return tuple(found)


def located(
    error: exceptions.InvalidValue, at: tuple[str | int, ...], record: Any
) -> list[exceptions.InvalidValue]:
    """Each failure of a record as a cell of the file: its position, then the field it names —
    a child by its position in the record.

        located(InvalidValue("must be > 0", ("lines", "0192…", "quantity")), (12,), invoice)
            ->  [InvalidValue("must be > 0", (12, "lines", 1, "quantity"), "content")]
    """
    failures = error.failures if isinstance(error, exceptions.ChecksFailed) else (error,)
    return [
        exceptions.InvalidValue(
            str(one), (*at, *_positions(record, one.path)), "content", one.rule
        )
        for one in failures
    ]


def applied(value: Any, records: dict[type, dict[str, Any]]) -> Any:
    """The values with each record they name resolved: the identity a key field holds, the record
    a relation holds."""
    if isinstance(value, Lookup):
        return records[value.entity][value.identity] if value.record else value.identity
    if isinstance(value, dict):
        return {name: applied(one, records) for name, one in value.items()}
    if isinstance(value, list):
        return [applied(one, records) for one in value if one is not None]
    return value


class EntityImports[T](EntityReads[T]):
    """The Feature that answers `Import` for one entity — and every read `EntityReads` answers,
    so one Feature can serve an entity's screens and its imports.

    @sales.feature([QueryListPartners, CommandImportPartners])
    class PartnerImports(EntityImports[Partner]):
        def build(self, dto: CommandImportPartners, batch: list[ImportRecord]) -> None:
            super().build(dto, batch)           # the file's values on each record, judged
            ...                                 # then a rule of this import alone
    """

    external_keys: ClassVar[type[ExternalKey]] = ExternalKey
    """Where the external keys are: `ExternalKey`, or the bounded context's subclass of it when
    two contexts share a mapper registry."""

    def rows(self, dto: Any) -> Iterator[ImportRow]:
        """The rows of the file, as its format reads them. Override it to read a format of
        its own — each row's cells nested, as JSON writes a record."""
        return rows_of(dto.content, dto.format, dto.sheet, dto.columns, dto.encoding)

    def records(self, dto: Any, rows: Iterable[ImportRow]) -> Iterator[ImportRecord]:
        """Each record of the file, read as a record of the entity: its values checked and
        marked (`shaped`), and the keys the file names it by — a record that does not read
        carries why."""
        entity, _ = declared_by(type(dto))
        natural, identity = key_of(entity), identity_name(entity)
        for at, values in whole_records(entity, rows, dto.format in FLAT):
            one = ImportRecord(at)
            try:
                own = {name: values.pop(name, None) for name in (KEY, identity)}
                one.values = shaped(entity, values, dto.decimal)
                own[natural] = one.values.get(natural) if natural != identity else None
                one.keys = [(by, str(key)) for by, key in own.items() if not is_blank(key)]
            except exceptions.InvalidValue as error:
                one.failure = error
            yield one

    def identities(
        self, dto: Any, kind: type, by: str, keys: list[str]
    ) -> dict[tuple[type, str, str], list[str]]:
        """The identities of the records of `kind` each key names, in one search: by the
        external key in the import's origin, or by the field `by`. A key no record holds names
        none; one two records hold names both.

            identities(dto, Country, "code", ["BO", "XX"])
                ->  {(Country, "code", "BO"): ["0192…"], (Country, "code", "XX"): []}
        """
        repository = self.reads_from()
        found: dict[tuple[type, str, str], list[str]] = {(kind, by, one): [] for one in keys}
        page = Pagination(limit=max(len(keys), 1))
        if by == KEY:
            criteria = Criteria(
                selection=All(
                    all=[
                        Condition(field="origin", value=dto.origin),
                        Condition(field="entity_type", value=kind.__name__),
                        Condition(field="key", operator=Operator.IN, value=keys),
                    ]
                ),
                pagination=page,
                count=CountMode.NONE,
            )
            for named in repository.fetch_all(self.external_keys, criteria):
                found[(kind, by, named.key)].append(named.entity_id)
            return found
        reader = adapter_of(model_of(kind).annotations[by])
        typed = []
        for one in keys:
            try:
                typed.append(reader.validate_python(one))
            except ValueError:
                continue
        if not typed:
            return found
        criteria = Criteria(
            selection=Condition(field=by, operator=Operator.IN, value=typed),
            pagination=page,
            count=CountMode.NONE,
        )
        for record in repository.fetch_all(kind, criteria):
            found.setdefault((kind, by, str(getattr(record, by))), []).append(
                str(identity_of(record))
            )
        return found

    def resolve(
        self,
        dto: Any,
        batch: list[ImportRecord],
        keys: dict[tuple[type, str, str], list[str]],
    ) -> None:
        """Every key of the batch turned into an identity: each record's own — finding the
        stored record it updates — and each record it names.

        1. The keys not known yet are searched, one search per entity and kind of key: the
           records' own for this batch alone, the references kept in `keys` for the whole import.
        2. Each record, in the file's order:
           2.1 found by the first of its keys a stored record holds — two records holding it is
               refused —; a key repeated earlier in the batch is refused;
           2.2 `mode` refuses one that exists, or one that does not;
           2.3 a new one takes the identity the file gives, or a new one; when the entity
               references itself, its keys are kept so a record below finds it;
           2.4 each record it names takes the identity its key names, or is refused naming it.
        3. The stored records the batch updates, read in one search with the relations the file
           writes and those their computations read.
        Final: each record with its identity and outcome, or its failure.
        """
        entity, _ = declared_by(type(dto))
        identity = identity_name(entity)
        alive = [one for one in batch if one.failure is None]
        named: dict[str, set[str]] = {}
        asked: dict[tuple[type, str], set[str]] = {}
        for one in alive:
            for by, key in one.keys:
                named.setdefault(by, set()).add(key)
            for lookup in lookups_in(one.values):
                if (lookup.entity, lookup.by, lookup.key) not in keys:
                    asked.setdefault((lookup.entity, lookup.by), set()).add(lookup.key)
        own: dict[tuple[type, str, str], list[str]] = {}
        for by, wanted in named.items():
            own.update(self.identities(dto, entity, by, sorted(wanted)))
        for (kind, by), wanted in asked.items():
            keys.update(self.identities(dto, kind, by, sorted(wanted)))
        remembers = any(
            lookup.entity is entity for one in alive for lookup in lookups_in(one.values)
        )
        seen: dict[tuple[str, str], tuple[str | int, ...]] = {}
        for one in alive:
            try:
                matched = ""
                for by, key in one.keys:
                    if (by, key) in seen:
                        raise exceptions.InvalidValue(
                            f"{by} {key!r} is repeated: the record at "
                            f"{'/'.join(map(str, seen[(by, key)]))} has it too",
                            (by,),
                        )
                    found = own.get((entity, by, key), [])
                    if len(found) > 1:
                        raise exceptions.InvalidValue(
                            f"{len(found)} {entity.__name__} have {by} {key!r}", (by,)
                        )
                    matched = matched or (found[0] if found else "")
                    seen[(by, key)] = one.at
                one.new_key = any(
                    by == KEY and not own.get((entity, KEY, key)) for by, key in one.keys
                )
                naming = f"{one.keys[0][0]} {one.keys[0][1]!r}" if one.keys else "no key"
                where = (one.keys[0][0],) if one.keys else ()
                if matched and dto.mode == ImportMode.CREATE:
                    raise exceptions.InvalidValue(
                        f"a {entity.__name__} with {naming} exists already", where
                    )
                if not matched and dto.mode == ImportMode.UPDATE:
                    raise exceptions.InvalidValue(f"no {entity.__name__} has {naming}", where)
                given = dict(one.keys).get(identity, "")
                one.identity = matched or given or new_entity_id()
                one.outcome = ImportOutcome.UPDATED if matched else ImportOutcome.CREATED
                if remembers and not matched:
                    for by, key in [*one.keys, (identity, one.identity)]:
                        keys[(entity, by, key)] = [one.identity]
                for lookup in lookups_in(one.values):
                    found = keys.get((lookup.entity, lookup.by, lookup.key), [])
                    if len(found) != 1:
                        where = (
                            ", stored or earlier in the file"
                            if lookup.entity is entity
                            else ""
                        )
                        raise exceptions.InvalidValue(
                            f"{len(found) or 'no'} {lookup.entity.__name__} "
                            f"{'have' if found else 'has'} {lookup.by} {lookup.key!r}{where}",
                            lookup.path,
                        )
                    lookup.identity = found[0]
            except exceptions.InvalidValue as error:
                one.failure = error
        updated = [
            one
            for one in alive
            if one.failure is None and one.outcome == ImportOutcome.UPDATED
        ]
        if not updated:
            return
        slots = slots_of(entity)
        written = sorted(
            {
                name
                for one in updated
                for name in one.values
                if slots[name].holds == Holds.RECORDS
            }
        )
        criteria = with_computed_relations(
            entity,
            Criteria(
                selection=Condition(
                    field=identity,
                    operator=Operator.IN,
                    value=[one.identity for one in updated],
                ),
                specification=(
                    Specification({name: Criteria() for name in written}) if written else None
                ),
                pagination=Pagination(limit=len(updated)),
                count=CountMode.NONE,
            ),
        )
        stored = {
            str(identity_of(record)): record
            for record in self.reads_from().fetch_all(entity, criteria)
        }
        for one in updated:
            one.stored = stored.get(one.identity)
            if one.stored is None:
                by, key = one.keys[0]
                one.failure = exceptions.InvalidValue(
                    f"the {entity.__name__} {by} {key!r} names does not exist anymore", (by,)
                )

    def build(self, dto: Any, batch: list[ImportRecord]) -> None:
        """Each record of the batch built: the stored one, or a new one under its identity, with
        the file's values on it (`apply_values`), its computed fields up to date and every
        `@check` judged (`recompute_whole`) — what a save would refuse is refused here, before
        anything is written. A stored record the file changes nothing of is `UNCHANGED`.

        The records a relation holds (a many-to-many) are read in one search per entity for the
        whole batch."""
        entity, _ = declared_by(type(dto))
        alive = [one for one in batch if one.failure is None]
        wanted: dict[type, set[str]] = {}
        for one in alive:
            for lookup in lookups_in(one.values):
                if lookup.record:
                    wanted.setdefault(lookup.entity, set()).add(lookup.identity)
        records: dict[type, dict[str, Any]] = {}
        for kind, ids in wanted.items():
            criteria = Criteria(
                selection=Condition(
                    field=identity_name(kind), operator=Operator.IN, value=sorted(ids)
                ),
                pagination=Pagination(limit=len(ids)),
                count=CountMode.NONE,
            )
            records[kind] = {
                str(identity_of(record)): record
                for record in self.reads_from().fetch_all(kind, criteria)
            }
        for one in alive:
            try:
                record = one.stored
                if record is None:
                    record = blank(extensions.class_for(entity, set(one.values)))
                    setattr(record, identity_name(entity), one.identity)
                one.record = record
                changed = apply_values(record, applied(one.values, records))
                recompute_whole(record)
                if one.stored is not None and not changed:
                    one.outcome = ImportOutcome.UNCHANGED
            except exceptions.InvalidValue as error:
                one.failure = error

    def write(self, dto: Any, batch: list[ImportRecord]) -> None:
        """The batch saved: the records created or changed, then the external keys the store
        did not hold — in the unit of work of the import, so a record and its key are stored
        together or not at all."""
        entity, _ = declared_by(type(dto))
        repository = self.reads_from()
        alive = [one for one in batch if one.failure is None]
        changed = [
            one.record
            for one in alive
            if one.outcome in (ImportOutcome.CREATED, ImportOutcome.UPDATED)
        ]
        if changed:
            repository.save(changed)
        named = [
            self.external_keys(
                origin=dto.origin,
                entity_type=entity.__name__,
                entity_id=one.identity,
                key=dict(one.keys)[KEY],
            )
            for one in alive
            if one.new_key
        ]
        if named:
            repository.save(named)

    def import_records(self, dto: Any) -> Any:
        """The file imported, batch by batch, in one unit of work.

        1. Each batch is resolved and built; its invalid records are located in the file.
        2. It is written unless the import is a dry run, or a record was invalid and the import
           does not skip them: from then on the file is only read, so the refusal names every
           invalid record, not only the first.
        3. A store that does not transact writes batch by batch, and says so once.
        Final: `ImportRefused` with every failure — nothing written —, or what was done.
        """
        entity, response = declared_by(type(dto))
        repository = self.reads_from()
        transacts = isinstance(repository, Transacts)
        if not transacts and not dto.dry_run:
            logger.warning(
                f"{type(repository).__name__} does not transact: the import of {entity.__name__} "
                "writes batch by batch, and a failure leaves the batches before it written"
            )
        failures: list[exceptions.InvalidValue] = []
        done: Counter[ImportOutcome] = Counter()
        writing = not dto.dry_run
        keys: dict[tuple[type, str, str], list[str]] = {}
        if dto.dry_run:
            unit = read_only()
        else:
            unit = repository.context() if transacts else nullcontext()
        with unit:
            for chunk in batched(self.records(dto, self.rows(dto)), dto.batch_size):
                batch = list(chunk)
                self.resolve(dto, batch, keys)
                self.build(dto, batch)
                invalid = False
                for one in batch:
                    if one.failure is not None:
                        invalid = True
                        failures.extend(located(one.failure, one.at, one.record))
                writing = writing and (dto.skip_invalid or not invalid)
                if writing:
                    self.write(dto, batch)
                done.update(
                    one.outcome for one in batch if one.failure is None and one.outcome
                )
            if failures and not dto.skip_invalid:
                raise exceptions.ImportRefused(tuple(failures))
        return response(
            created=done[ImportOutcome.CREATED],
            updated=done[ImportOutcome.UPDATED],
            unchanged=done[ImportOutcome.UNCHANGED],
            skipped=[
                FieldError(
                    pointer=pointer((one.field, *one.path)), detail=str(one), rule=one.rule
                )
                for one in failures
            ],
        )

    def preview(self, dto: Any) -> Any:
        """A dry run of an `Import`: the whole file read, resolved and judged, nothing written —
        refused as the import would refuse it, or what it would do. Any other DTO is the
        `PreviewEntity` `EntityReads` answers."""
        if not isinstance(dto, Import):
            return super().preview(dto)
        answer = self.import_records(dto)
        skipped = f", skip {len(answer.skipped)}" if answer.skipped else ""
        return Preview(
            text=(
                f"Would create {answer.created}, update {answer.updated} and leave "
                f"{answer.unchanged} unchanged{skipped}"
            ),
            data=answer.model_dump(mode="json"),
        )

    def execute(self, dto: Any) -> Any:
        """An `Import` is imported; every other DTO is a read `EntityReads` answers."""
        if isinstance(dto, Import):
            return self.import_records(dto)
        return super().execute(dto)
