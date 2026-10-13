"""The type of a stored row: the most derived extension it has, worked out when it is read.

    Move ← MoveDelivery ← MoveTracking        three classes, three tables, one id

    a row in move only                   →  Move
    a row in move and move_delivery      →  MoveDelivery
    a row in all three                   →  MoveTracking

There is no column that says it. `ExtensionType` is a SQL expression the mapper reads as the
discriminator of the root entity, and it is written when a statement is compiled from the
extensions registered *by then* — so a module installed after the core was mapped is part of the
answer without the core knowing it exists.

An extension whose columns live in the root's own table has no row to look for: it is present when
any column it owns is not NULL.
"""

from sqlalchemy import String, Table, case, exists, literal, or_, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import ENGINES

EXTENSIONS: dict[type, list[tuple[int, str, type, ColumnElement[bool]]]] = {}
"""Per root entity: its extensions as (depth, identity, class, how to tell a row has it)."""

STRICT: set[type] = set()
"""Roots that answer only themselves: a read of the root never returns an extension."""

TYPE_ATTRIBUTE = "_extension_type"
"""Where the expression is mapped on the record: not a field, never written or described."""

ROOT_OF: dict[type, type] = {}
"""The entity at the top of each mapped class's chain of extensions (itself, for a root)."""


def identity_of(entity: type) -> str:
    return f"{entity.__module__}.{entity.__qualname__}"


def has_the_row_of(root_table: Table, table: Table) -> ColumnElement[bool]:
    """Whether the root's row has a row in the extension's own table."""
    key = list(table.primary_key.columns)[0]
    root_key = list(root_table.primary_key.columns)[0]
    return exists(select(literal(1)).where(key == root_key).correlate(root_table))


def has_a_column_set(table: Table, columns: list[str]) -> ColumnElement[bool]:
    """Whether any column the extension owns, inside the root's table, holds a value."""
    return or_(*[table.c[name].isnot(None) for name in columns])


def register(root: type, entity: type, depth: int, proof: ColumnElement[bool]) -> None:
    """Notes an extension of `root`; the deepest are asked first."""
    EXTENSIONS.setdefault(root, []).append((depth, identity_of(entity), entity, proof))
    EXTENSIONS[root].sort(key=lambda found: -found[0])
    # A compiled `select(Root)` is keyed by the mapper, not by what the mapper reads: one
    # compiled before this extension existed would go on answering without it.
    for engine in ENGINES:
        engine.clear_compiled_cache()


Entities = type
"""`type` is the name of the SQL type inside `ExtensionType`; this one stays the builtin."""


class ExtensionType(ColumnElement):
    """The identity of the most derived class a row of `root` has."""

    type = String()
    inherit_cache = True
    _traverse_internals: list = []

    def __init__(self, root: Entities, within: Entities | None = None) -> None:
        super().__init__()
        self.root = root
        self.within = within or root

    def _gen_cache_key(self, anon_map, bindparams):  # type: ignore[no-untyped-def]  # noqa: ARG002
        # The statement is compiled from what is registered, so what is registered is part
        # of its identity: a module installed later never meets a stale compiled statement.
        known = tuple(found[1] for found in EXTENSIONS.get(self.root, ()))
        return (
            self.__class__,
            self.root.__qualname__,
            self.within.__qualname__,
            self.root in STRICT,
            known,
        )


@compiles(ExtensionType)
def compile_extension_type(element: ExtensionType, compiler, **kw):  # type: ignore[no-untyped-def]
    # Reading from an extension decides only among that extension and what extends it: a sibling
    # with a row of its own is another kind of the same record, not a level below this one.
    root_identity = identity_of(element.within)
    found = (
        []
        if element.root in STRICT
        else [
            one
            for one in EXTENSIONS.get(element.root, [])
            if issubclass(one[2], element.within)
        ]
    )
    if not found:
        return compiler.process(literal(root_identity, String()), **kw)
    branches = [(proof, literal(identity, String())) for _, identity, _, proof in found]
    return compiler.process(case(*branches, else_=literal(root_identity, String())), **kw)


__all__ = [
    "EXTENSIONS",
    "ROOT_OF",
    "STRICT",
    "TYPE_ATTRIBUTE",
    "ExtensionType",
    "identity_of",
    "register",
]
