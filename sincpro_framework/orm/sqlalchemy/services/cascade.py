"""What `save` and `remove` do with the children a root holds.

    workspace.repositories = [a, b]           an assignment: these are its children now
    repository.save(workspace)                →  a and b written, workspace.code on their key
                                                 the ones the last reading saw and the root no
                                                 longer holds settled: a NOT NULL key deletes
                                                 them, a nullable one is set to NULL
    repository.remove(workspace)              →  its children taken along first — the parts of an
                                                 aggregate go with it (`owned=False` excepted)
    invoice.lines[0].amount = 5; save(invoice)   →  the line written, and the invoice's version
                                                 raised with it: a part moves its aggregate

The framework's relations are its own descriptors, not SQLAlchemy relationships, so the cascade
is planned here, before the flush, and handed to the write path every aggregate takes — hooks,
version check, stamping. SQLAlchemy writes the rows in the order planned.

**An orphan is a child that was read and is no longer held** — never a stored row nobody read.
Assigning over a whole reading settles exactly what that reading saw; a relation that was only
read removes nothing; an assignment with no whole reading behind it saves and says so in the
log. Deleting what a filter hid, or what another transaction added after the read, is the bug
every ORM that diffs a collection has shipped once.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import Table
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from sincpro_framework.ddd.entity.entity_collection import identity_name, identity_of
from sincpro_framework.ddd.entity.relations import key_pair
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.domain.registry import relations_of
from sincpro_framework.orm.sqlalchemy.domain.relations import (
    HELD,
    READ,
    RESOLVED,
    Held,
    Orphans,
    held,
    read_before,
)
from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.services.upsert import unique_on
from sincpro_framework.sincpro_logger import logger

WRITTEN = {Held.ASSIGNED: Held.WHOLE, Held.BLIND: Held.CUT}
"""What an assignment becomes once written: an instruction carried out, not one to repeat."""


@dataclass(frozen=True)
class Owned:
    """One to-many a root owns, read off its declaration: the root's field, the child's field
    that holds it, and what an orphan becomes."""

    name: str
    related: type
    here: str
    there: str
    orphans: Orphans


@dataclass
class Plan:
    """What one `save` or `remove` writes: the children detached (their key set to NULL), the
    levels to delete — the records first, their descendants after — the records to insert or
    update, the assignments it carries out, and the stored roots whose parts it writes."""

    detached: list[Any] = field(default_factory=list)
    removals: list[list[Any]] = field(default_factory=list)
    writes: list[Any] = field(default_factory=list)
    carried_out: list[tuple[Any, str]] = field(default_factory=list)
    moved: list[Any] = field(default_factory=list)

    @property
    def removed(self) -> list[Any]:
        return [one for level in self.removals for one in level]

    @property
    def stored(self) -> list[Any]:
        """Every record this plan inserts or updates, the detached included."""
        return [*self.detached, *self.writes]

    def version_moves(self) -> None:
        """Marks every stored root whose parts this plan writes as changed, so its row is updated
        with the version check and its version raised — even when none of its own fields moved.

        **The version is the aggregate's, not the row's** (Vernon). Without this, two requests
        editing different lines of one invoice both commit: neither touched the invoice's row, so
        neither was checked, and an invariant over the lines — a total, a limit — breaks unseen.
        The root is marked through `updated_at`, which the write stamps anyway: SQLAlchemy cannot
        be told to raise `version_id_col` alone.
        """
        for root in self.moved:
            mapper = sa_inspect(root).mapper
            if mapper.version_id_col is None:
                continue
            stamp = "updated_at" if "updated_at" in mapper.attrs else _plain_column(mapper)
            if stamp is not None:
                flag_modified(root, stamp)

    def written(self) -> None:
        """Marks every assignment this plan carried out as written: the set it wrote is now
        what was read, and the next `save` does not settle it again."""
        for record, name in self.carried_out:
            how = held(record, name)
            after = WRITTEN.get(how) if how is not None else None
            if after is not None:
                record.__dict__[HELD][name] = after
                children = record.__dict__[RESOLVED][name] or ()
                record.__dict__.setdefault(READ, {})[name] = frozenset(
                    identity_of(child) for child in children
                )


def _plain_column(mapper: Any) -> str | None:
    """A column of the root that is neither its key nor its version — what marks the row
    changed when the mapping has no `updated_at`."""
    keys = {column.key for column in mapper.primary_key}
    version = mapper.version_id_col.key
    for column in mapper.column_attrs:
        if column.key not in keys and column.columns[0].key != version:
            return column.key
    return None


def _refused(owned: Owned, root: type, children: list[Any], moment: str) -> ContractViolation:
    return ContractViolation(
        f"{moment} {len(children)} {owned.related.__name__} of {root.__name__}.{owned.name}, and "
        "the relation does not say what that does — remove them first, or declare it: "
        f"Relation.foreign_key({owned.related.__name__}, …, orphans=Orphans.DELETE) takes them "
        "along, Orphans.DETACH sets their key to NULL"
    )


def _held_by_the_root(model: type, here: str, identity: str) -> bool:
    """Context: a to-many joined on a field the root holds unique — its identity, a business key
    — is the root's; one joined on a field many roots share (a tax code, a category) reaches the
    children of other roots, and writing or removing through it would touch theirs."""
    if here == identity:
        return True
    mapper = sa_inspect(model)
    column = mapper.get_property(here).columns[0]
    return unique_on(cast(Table, mapper.local_table), {column.name})


def owned_by(model: type) -> list[Owned]:
    """The to-manys this aggregate writes. Context: read on every write rather than cached,
    because a class mapped later can still add a relation to one mapped before."""
    meta = describe(model)
    owned: list[Owned] = []
    for name, relation in relations_of(model).items():
        declared = meta.fields.get(name)
        to_many = declared is not None and declared.many and relation.kind == "foreign_key"
        if not to_many or getattr(relation, "owned", True) is False:
            continue
        here, there = key_pair(meta.identity, relation, many=True)
        if not _held_by_the_root(model, here, meta.identity):
            continue
        orphans = getattr(relation, "orphans", None) or Orphans.REFUSE
        owned.append(
            Owned(
                name,
                relation.related,
                here,
                there,
                orphans,
            )
        )
    return owned


def _read_without_flushing(session: Session, statement: Any) -> list[Any]:
    """Context: the roots' changes are not written yet and their hooks have not run; a flush
    here would write them before the rules saw them."""
    with session.no_autoflush:
        return list(session.scalars(statement))


def _by_model(records: Iterable[Any]) -> dict[type, list[Any]]:
    grouped: dict[type, list[Any]] = {}
    for one in records:
        grouped.setdefault(type(one), []).append(one)
    return grouped


def planned_removal(session: Session, records: list[Any]) -> Plan:
    """What removing these records takes with it: the levels to delete, the records first and
    their descendants after, and the children whose key is set to NULL instead.

    1. The records are the first level.
    2. Per level and per relation the level owns, one read of every stored child of the
       whole level, by the foreign key alone — a relation's scope narrows what it shows, not
       what depends on the root.
    3. A child whose key is nullable is detached; the rest are the next level.
    4. Final: repeat until a level owns nothing. A reference (`owned=False`) is left to its
       foreign key, which refuses the delete while children point at the root.
    """
    plan = Plan()
    seen: set[tuple[type, Any]] = set()
    level = records
    while level:
        level = [one for one in level if (type(one), identity_of(one)) not in seen]
        if not level:
            break
        seen.update((type(one), identity_of(one)) for one in level)
        plan.removals.append(level)
        below: list[Any] = []
        for model, roots in _by_model(level).items():
            for owned in owned_by(model):
                keys = {getattr(root, owned.here) for root in roots} - {None}
                if not keys:
                    continue
                key = getattr(owned.related, owned.there)
                children = _read_without_flushing(
                    session, select(owned.related).where(key.in_(keys))
                )
                if children and owned.orphans is Orphans.REFUSE:
                    raise _refused(owned, model, children, "removing it would leave")
                for child in children:
                    if owned.orphans is Orphans.DETACH:
                        setattr(child, owned.there, None)
                        plan.detached.append(child)
                    else:
                        below.append(child)
        level = below
    return plan


def _changed(record: Any) -> bool:
    """Whether a child has anything to write: never stored, or changed since it was read."""
    if getattr(record, "is_new", False):
        return True
    state = sa_inspect(record, raiseerr=False)
    return state is None or state.transient or state.pending or state.modified


def _refuse_a_child_removed_before(records: list[Any]) -> None:
    for one in records:
        state = sa_inspect(one, raiseerr=False)
        if state is not None and (state.deleted or state.was_deleted):
            raise ContractViolation(
                f"{type(one).__name__} {identity_of(one)} was removed earlier in this unit of "
                "work, as an orphan of the root that held it; to move children between roots, "
                "save both in one call — save([old_root, new_root])"
            )


def planned_save(session: Session, records: list[Any]) -> Plan:
    """Everything one `save` of these roots writes.

    1. Walk each root and, through every relation it writes, its children, depth first: the
       roots are always written, a child only when it is new or changed.
       1.1 Each child gets the root's key on the field that holds it.
       1.2 A relation nobody read is skipped; a read one only saves what it holds; a blind
           assignment does the same and is logged.
    2. Per relation a stored root assigned over a whole reading, one read of the children that
       reading saw and no record of this save holds, still pointing at the root that read them:
       the orphans.
    3. A detached orphan has its key set to NULL; a deleted one is a removal and takes what it
       owns with it.
    4. Final: every stored root of this save whose parts are written, detached or removed is
       marked moved — its version is raised with them (`Plan.version_moves`).
    """
    plan = Plan()
    seen: set[int] = set()
    held_ids: dict[type, set[Any]] = {}
    settling: dict[tuple[type, str], tuple[Owned, dict[Any, set[Any]]]] = {}
    moved: dict[int, Any] = {}
    root_by_key: dict[tuple[type, str, Any], list[Any]] = {}

    def visit(record: Any, explicit: bool, root: Any) -> None:
        if id(record) in seen:
            return
        seen.add(id(record))
        held_ids.setdefault(type(record), set()).add(identity_of(record))
        if explicit or _changed(record):
            plan.writes.append(record)
            if not explicit:
                moved[id(root)] = root
        for owned in owned_by(type(record)):
            how = held(record, owned.name)
            if how is None:
                continue
            key = getattr(record, owned.here)
            for child in record.__dict__[RESOLVED][owned.name] or ():
                if getattr(child, owned.there) != key:
                    setattr(child, owned.there, key)
                visit(child, explicit=False, root=root)
            if how in WRITTEN:
                plan.carried_out.append((record, owned.name))
            if how is Held.BLIND:
                logger.warning(
                    f"{type(record).__name__}.{owned.name} was assigned with no whole reading "
                    "behind it (a page, a filter, or outside context()): its children were "
                    "saved and none was removed — assign it inside context() to rebuild it"
                )
            elif how is Held.ASSIGNED and not getattr(record, "is_new", False):
                _, read_by_key = settling.setdefault((type(record), owned.name), (owned, {}))
                read_by_key.setdefault(key, set()).update(read_before(record, owned.name))
                root_by_key.setdefault((type(record), owned.name, key), []).append(root)

    for one in records:
        visit(one, explicit=True, root=one)
    _refuse_a_child_removed_before(plan.writes)

    orphans: list[Any] = []
    for (owner, name), (owned, read_by_key) in settling.items():
        held_here = held_ids.get(owned.related, set())
        gone_by_key = {
            key: read - held_here for key, read in read_by_key.items() if key is not None
        }
        gone = set().union(*gone_by_key.values()) if gone_by_key else set()
        if not gone:
            continue
        identity = getattr(owned.related, identity_name(owned.related))
        key = getattr(owned.related, owned.there)
        statement = select(owned.related).where(identity.in_(gone), key.in_(gone_by_key))
        # An orphan of the root that read it: a child another writer moved to a root of this
        # same batch is that root's now, not an orphan.
        dropped = [
            child
            for child in _read_without_flushing(session, statement)
            if identity_of(child) in gone_by_key.get(getattr(child, owned.there), ())
        ]
        if dropped and owned.orphans is Orphans.REFUSE:
            raise _refused(owned, owner, dropped, "the assignment drops")
        for child in dropped:
            for root in root_by_key.get((owner, name, getattr(child, owned.there)), ()):
                moved[id(root)] = root
        for child in dropped:
            if owned.orphans is Orphans.DETACH:
                setattr(child, owned.there, None)
                plan.detached.append(child)
            else:
                orphans.append(child)
    if orphans:
        taken = planned_removal(session, orphans)
        plan.removals = taken.removals
        plan.detached.extend(taken.detached)
    plan.moved = [root for root in moved.values() if not getattr(root, "is_new", False)]
    return plan
