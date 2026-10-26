"""One model's batch as one `INSERT … ON CONFLICT … RETURNING`, built from what the table holds —
with no repository in it. `Writing.upsert` orchestrates: it checks the scope, runs the hooks,
executes, and counts what came back.

    refuse(...)        what cannot be upserted, said before any statement exists
    rows(...)          each record as the row it becomes; a new one at version 1
    overwritten(...)   the columns a conflict overwrites
    statement(...)     the INSERT, its conflict clause, and the identities it wrote back

Postgres and SQLite (3.35+, for `RETURNING`) speak it. MySQL's `ON DUPLICATE KEY` is left out on
purpose: it matches any unique key rather than the one named, cannot leave a conflicting row as it
is, and reports an affected-rows count that says neither what was inserted nor what was updated.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import UniqueConstraint

from sincpro_framework.data_layer import exceptions

INSERTS: dict[str, Any] = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}
"""The dialects an upsert speaks, each with its own `INSERT … ON CONFLICT`."""


def unique_on(table: Table, columns: set[str]) -> bool:
    """Whether the table holds these columns unique together: its key, a unique constraint, a
    unique index, or one column declared unique.

        unique_on(invoice, {"id"})                    ->  True     the primary key
        unique_on(invoice, {"company_id", "number"})  ->  True     UniqueConstraint over both
        unique_on(invoice, {"number"})                ->  False    unique only with company_id

    The set must be exactly the constraint's columns: a subset or a superset is not unique.
    """
    if {column.name for column in table.primary_key.columns} == columns:
        return True
    for constraint in table.constraints:
        if (
            isinstance(constraint, UniqueConstraint)
            and {c.name for c in constraint.columns} == columns
        ):
            return True
    for index in table.indexes:
        if index.unique and {c.name for c in index.columns} == columns:
            return True
    return len(columns) == 1 and bool(table.c[next(iter(columns))].unique)


def refuse(
    model: type,
    table: Table,
    extends_another: bool,
    dialect: str,
    names: Mapping[str, str],
    on: Sequence[str],
    overwrite: Sequence[str] | None,
    fixed: set[str],
) -> None:
    """Raises `ProgrammingError` for a batch no single upsert can write correctly.

    Context: an upsert skips the aggregate on purpose, so everything it could get silently
    wrong is refused up front, before a statement is built.

    1. An aggregate that extends another across two tables: one INSERT cannot write it.
    2. A dialect with no `INSERT … ON CONFLICT` here (only Postgres and SQLite).
    3. A field in `on` or `overwrite` the model has no column for.
    4. An `overwrite` naming a column the framework sets (`fixed`).
    Final: `on` columns the table does not hold unique — a conflict would never fire, and
    every call would insert.

        refuse(Rate, rate, False, "postgresql", names, ["currency", "day"], None, fixed)
            ->  None, it can be upserted
        refuse(Rate, rate, False, "mysql", names, ["currency", "day"], None, fixed)
            ->  ProgrammingError("mysql has no upsert this repository speaks; …")
        refuse(..., ["currency", "day"], ["version"], {"version", ...})
            ->  ProgrammingError("upsert cannot overwrite version: the framework sets it")

    The first rule broken is the one raised.
    """
    if extends_another:
        raise exceptions.ProgrammingError(
            f"{model.__name__} extends another aggregate across two tables; one INSERT cannot "
            "upsert it — save it instead"
        )
    if dialect not in INSERTS:
        raise exceptions.ProgrammingError(
            f"{dialect} has no upsert this repository speaks; Postgres and SQLite do"
        )
    unknown = [name for name in [*on, *(overwrite or ())] if name not in names]
    if unknown:
        raise exceptions.ProgrammingError(
            f"{model.__name__} has no column {', '.join(unknown)}"
        )
    set_by_framework = sorted(fixed & set(overwrite or ()))
    if set_by_framework:
        raise exceptions.ProgrammingError(
            f"upsert cannot overwrite {', '.join(set_by_framework)}: the framework sets it"
        )
    if not unique_on(table, {names[name] for name in on}):
        raise exceptions.ProgrammingError(
            f"upsert on {', '.join(on)} needs the table to hold them unique; {table.name} does "
            "not, so a conflict would never be detected"
        )


PRESERVED_BY_DEFAULT = {"archived_at", "created_by"}
"""What a conflict does not overwrite unless named: an archived row stays archived, and who
created a row stays who did."""


def one_per_key(records: Iterable[Any], on: Sequence[str]) -> list[Any]:
    """The batch with one record per key, the last one. Context: a key with a NULL in it never
    conflicts in the database, so such records are each their own.

        one_per_key([Rate("USD", 6.96), Rate("EUR", 7.5), Rate("USD", 6.97)], ["currency"])
            ->  [Rate("USD", 6.97), Rate("EUR", 7.5)]
        one_per_key([Rate(None, 1), Rate(None, 2)], ["currency"])
            ->  [Rate(None, 1), Rate(None, 2)]

    A key holds the place it first took; its record is the last one given.
    """
    latest: dict[Any, Any] = {}
    for one in records:
        values = tuple(getattr(one, name) for name in on)
        latest[values if None not in values else ("unkeyed", id(one))] = one
    return list(latest.values())


def rows(
    records: Iterable[Any], names: Mapping[str, str], now: datetime
) -> list[dict[str, Any]]:
    """Each record as its row: a new one at version 1, stamped when it was created.

        rows([Rate(currency="USD", value=6.96, version=0, created_at=t1, updated_at=None)],
             {"currency": "currency", "value": "value", "version": "version", ...}, now)
            ->  [{"currency": "USD", "value": 6.96, "version": 1, "updated_at": t1, ...}]

    A record with no `created_at` is stamped `now`; a version already set is left as it is.
    """
    built = []
    for one in records:
        row = {column: getattr(one, key) for key, column in names.items()}
        if "version" in row and not row["version"]:
            row["version"] = 1
        if "updated_at" in row and row["updated_at"] is None:
            row["updated_at"] = getattr(one, "created_at", None) or now
        built.append(row)
    return built


def overwritten(
    names: Mapping[str, str],
    on: Sequence[str],
    overwrite: Sequence[str] | None,
    fixed: set[str],
    scope_fields: set[str],
) -> list[str]:
    """The columns a conflict overwrites: the ones named, or every one but the key, the
    framework's own and the scope's — a narrowed repository never moves a row out of it."""
    if overwrite is not None:
        return [names[name] for name in overwrite if name not in scope_fields]
    preserved = {
        names[name]
        for name in [*on, *fixed, *scope_fields, *PRESERVED_BY_DEFAULT]
        if name in names
    }
    return [column for column in names.values() if column not in preserved]


def statement(
    dialect: str,
    table: Table,
    built: list[dict[str, Any]],
    keys: set[str],
    columns: list[str],
    now: datetime,
    bound: Any,
    identity: str,
) -> Any:
    """The upsert, answering the identity of every row it inserted or overwrote.

    1. A conflicting row gets the columns overwritten, its version raised and `updated_at`
       stamped — only where `bound` (the repository's scope) holds for the stored row.
    2. Final: with nothing to overwrite, a conflict leaves the stored row as it is.

        statement("postgresql", rate, built, {"currency", "day"}, ["value"], now, None, "id")
            ->  INSERT INTO rate (…) VALUES (…), (…)
                ON CONFLICT (currency, day) DO UPDATE SET value = excluded.value,
                    version = rate.version + 1, updated_at = :now
                RETURNING rate.id
        the same with no columns to overwrite
            ->  … ON CONFLICT (currency, day) DO NOTHING RETURNING rate.id

    A row a conflict left alone is not returned, so what comes back is what was written.
    """
    insert = INSERTS[dialect](table).values(built)
    changes: dict[str, Any] = {column: insert.excluded[column] for column in columns}
    if changes and "version" in table.c:
        changes["version"] = table.c.version + 1
    if changes and "updated_at" in table.c:
        changes["updated_at"] = now
    if changes:
        conflict = insert.on_conflict_do_update(
            index_elements=sorted(keys), set_=changes, where=bound
        )
    else:
        conflict = insert.on_conflict_do_nothing(index_elements=sorted(keys))
    return conflict.returning(table.c[identity])
