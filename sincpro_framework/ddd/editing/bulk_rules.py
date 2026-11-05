"""What the writes past the aggregate — `upsert`, `update_all`, `remove_all` — do with its rules:
they keep the ones they can judge, and refuse, before anything is written, the ones they cannot,
unless the call skips the rules out loud (`ContextKey.SKIP_CHECKS`).

    unjudged_by_row(Sale, columns, {"id", "state"})         what an upsert cannot judge
        ->  (Unjudged("Sale.delivery_when_confirmed", ("delivery",)), …)
    unjudged_by_values(Sale, {"state"})                     what an update_all reaches
        ->  (Unjudged("Sale.delivery_when_confirmed", ("state",)),)
    unjudged_through(((Sale, "lines"),))                    a write to the rows of a line
        ->  (Unjudged("Sale.total_of", ("lines",), "total"), …)
    keep_or_skip(Sale, "update_all", found, "…")            refused, or said and let through

Context: a save holds the whole aggregate, so it computes and judges it (`recompute_whole`).
The doors past it write rows: an upsert holds each record but decides only part of its row —
a conflict keeps what it does not overwrite, and a row never carries the records a relation
holds —, and a write by criteria holds no record at all. Each door judges what it decides and
refuses what it cannot judge, so no rule is ever skipped without someone saying so. Nothing
here knows a database: the stores say what each write decides.
"""

import dataclasses
from collections.abc import Iterable, Sequence

from sincpro_framework.ddd.editing.editing import held_records, skipping_checks
from sincpro_framework.ddd.entity.entity_meta import FieldType, Meta
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.exceptions import ProgrammingError

APART = (FieldType.ONE2MANY, FieldType.MANY2MANY)
"""The fields whose records live in other rows: the lines of a one2many, the pairs of a
many2many. A write of one row never carries them."""

SKIP = "skip the checks for this call, with a reason (ContextKey.SKIP_CHECKS)"
"""The way out every refusal names: the one switch, which every write reads."""

UPSERT_REMEDY = (
    "Overwrite every field those rules read (update=…) where the row has it, or save the "
    "records instead"
)
"""How an upsert keeps the rules its row cannot judge."""

UPDATE_REMEDY = (
    "Save the records instead — read them, change them, save them, a page per flush — so "
    "each one is computed and judged"
)
"""How a write by criteria keeps the rules it cannot judge row by row."""

REMOVE_REMEDY = (
    "Remove them through the aggregate that owns them instead — read it, take them out, "
    "save it"
)
"""How the rows an aggregate owns leave without going around its rules."""


def kept_apart(meta: Meta) -> frozenset[str]:
    """The fields of `meta` a row of its own never carries.

    kept_apart(describe(Sale))     ->  frozenset({"lines", "tags"})
    kept_apart(describe(Rate))     ->  frozenset()
    """
    return frozenset(name for name, field in meta.fields.items() if field.type in APART)


def refuse_held_apart(record: object, apart: Iterable[str], verb: str) -> None:
    """Refuses a record that holds records a row never carries — lines, the pairs of a
    many2many —, because the write would drop them without a word. Nothing is read to know it:
    a relation nobody read holds none.

        refuse_held_apart(Sale(lines=[desk]), {"lines"}, "upsert")
            ->  ProgrammingError("upsert writes Sale as one row: Sale.lines holds 1 record it
                would never write — save it instead")
        refuse_held_apart(Sale(), {"lines"}, "upsert")          ->  None
    """
    for name in apart:
        held = held_records(record, name)
        if held:
            kind = type(record).__name__
            raise ProgrammingError(
                f"{verb} writes {kind} as one row: {kind}.{name} holds {held} "
                f"record{'s' if held > 1 else ''} it would never write — save it instead"
            )


@dataclasses.dataclass(frozen=True)
class Unjudged:
    """A rule a write cannot judge: its name, the fields that make it so, and, for a
    computation, the field it would leave as stored."""

    rule: str
    fields: tuple[str, ...]
    computes: str = ""
    why: str = ""

    def said(self) -> str:
        """The rule in a sentence: `Sale.total_of computes total from lines (the row does not
        carry them)`."""
        if not self.computes:
            said = f"{self.rule} watches {', '.join(self.fields)}"
        else:
            reads = [one for one in self.fields if one != self.computes]
            said = f"{self.rule} computes {self.computes}" + (
                f" from {', '.join(reads)}" if reads else ""
            )
        return f"{said} ({self.why})" if self.why else said


def _rules(entity: type) -> Iterable[tuple[str, tuple[str, ...], str]]:
    """Every rule of `entity` a save runs, as (name, fields it reads, field it computes):
    each check, and each computation that is not editable — an editable one only proposes a
    value, and a save stores the one given."""
    model = model_of(entity)
    for check in model.checks:
        yield f"{entity.__name__}.{check.method}", tuple(check.watches), ""
    for computation in model.computations:
        if not computation.editable:
            yield (
                f"{entity.__name__}.{computation.method}",
                tuple(computation.depends),
                computation.field,
            )


def unjudged_by_row(
    entity: type, row: Iterable[str], overwritten: Iterable[str]
) -> tuple[Unjudged, ...]:
    """The rules of `entity` an upsert cannot judge on the row it leaves. `row` is what an
    insert takes from the record — its columns —, `overwritten` what a conflict does — the
    fields it overwrites and the key. A rule that reads (or computes) the fields `R`:

    1. reads a field outside `row` — a relation, whose records live in other rows: unjudged;
    2. reads some overwritten fields and some a conflict keeps as stored: the row mixes the
       stored and the handed, so it is unjudged;
    3. otherwise it is judged on the record as it is handed, which is the row either way: an
       insert writes all of `R`, and a conflict overwrites all of it or none of it.

        unjudged_by_row(Sale, columns, columns)                       update omitted
            ->  (Unjudged("Sale.total_of", ("lines",), "total"),)     the row carries no lines
        unjudged_by_row(Sale, columns, {"id", "state"})               update=("state",)
            ->  (Unjudged("Sale.delivery_when_confirmed", ("delivery",)),)
        unjudged_by_row(Rate, columns, {"currency"})                  update=(): a conflict
            ->  ()                                                    writes nothing
    """
    carried = set(row)
    over = set(overwritten)
    found: list[Unjudged] = []
    for rule, reads, computes in _rules(entity):
        fields = (*reads, *((computes,) if computes else ()))
        apart = tuple(one for one in fields if one not in carried)
        kept = tuple(one for one in fields if one not in over)
        if apart:
            found.append(Unjudged(rule, apart, computes, "the row does not carry it"))
        elif kept and len(kept) < len(fields):
            found.append(Unjudged(rule, kept, computes, "a conflict keeps it as stored"))
    return tuple(found)


def unjudged_by_values(entity: type, values: Iterable[str]) -> tuple[Unjudged, ...]:
    """The rules of `entity` a write of these values to every row a filter matches reaches: a
    check that watches one of them, a computation that reads one or computes one. One
    statement holds no record, so it judges none of them.

        unjudged_by_values(Sale, {"state"})
            ->  (Unjudged("Sale.delivery_when_confirmed", ("state",)),)
        unjudged_by_values(Sale, {"note"})      ->  ()     no rule reads a note
        unjudged_by_values(Sale, {"total"})
            ->  (Unjudged("Sale.total_of", ("total",), "total"),)     a computed field set by hand
    """
    written = set(values)
    found: list[Unjudged] = []
    for rule, reads, computes in _rules(entity):
        reached = tuple(
            one for one in (*reads, *((computes,) if computes else ())) if one in written
        )
        if reached:
            found.append(Unjudged(rule, reached, computes))
    return tuple(found)


def unjudged_through(owners: Iterable[tuple[type, str]]) -> tuple[Unjudged, ...]:
    """The rules of the aggregates that own the rows a write reaches — `(Sale, "lines")` for a
    write to the table of `SaleLine` — that read the relation those rows are: a write to a line's
    row bypasses its sale, so a total that sums the lines, or a check over them, is not run.

        unjudged_through([(Sale, "lines")])
            ->  (Unjudged("Sale.total_of", ("lines",), "total"),
                 Unjudged("Sale.each_product_once", ("lines",)))
    """
    found: list[Unjudged] = []
    for owner, relation in owners:
        for rule, reads, computes in _rules(owner):
            if relation in reads:
                found.append(
                    Unjudged(
                        rule,
                        (relation,),
                        computes,
                        f"the rows written are the {relation} of a {owner.__name__}",
                    )
                )
    return tuple(found)


def keep_or_skip(entity: type, verb: str, unjudged: Sequence[Unjudged], remedy: str) -> None:
    """Refuses a write that cannot judge `unjudged`, before anything is written — unless the
    call skips the rules, which is then said, once, naming every rule left out.

        keep_or_skip(Sale, "update_all", (), "…")                    ->  None, nothing to judge
        keep_or_skip(Sale, "update_all", found, "Save the records instead")
            ->  ProgrammingError("update_all of Sale cannot judge its rules: Sale.delivery_when_
                confirmed watches state. Save the records instead, or skip the checks …")
        # inside bus.context(skip_checks="nightly close")
        keep_or_skip(Sale, "update_all", found, "…")
            ->  None; warning: "Sale written by update_all past its rules — Sale.delivery_
                when_confirmed watches state: nightly close (asked by ana)"

    A `ProgrammingError`, never a refusal of the values: the write is the wrong one for an
    entity with these rules whatever the rows hold, so the same call is refused every time —
    the first test that makes it says so.
    """
    if not unjudged:
        return
    rules = "; ".join(one.said() for one in unjudged)
    if skipping_checks(entity, f"written by {verb} past its rules — {rules}"):
        return
    raise ProgrammingError(
        f"{verb} of {entity.__name__} cannot judge its rules: {rules}. {remedy}, or {SKIP}"
    )
