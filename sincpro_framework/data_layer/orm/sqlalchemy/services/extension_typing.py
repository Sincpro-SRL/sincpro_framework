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

from sqlalchemy import Column, String, Table, case, exists, literal, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import ENGINES
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.extensions import identity_of

STRICT: set[type] = set()
"""Roots that answer only themselves: a read of the root never returns an extension."""

TYPE_ATTRIBUTE = "_extension_type"
"""Where the expression is mapped on the record: not a field, never written or described."""

STORED_ATTRIBUTE = "extension_type"
"""The column a core may declare to store the type of each row, instead of working it out."""

HIDDEN = frozenset({TYPE_ATTRIBUTE, STORED_ATTRIBUTE})
"""Attributes the engine keeps for the type of a row: not fields of the entity."""

STORED_TYPE: dict[type, Column] = {}
"""Per root, the column that stores the type of its rows, when its table declares one."""

ROOT_OF: dict[type, type] = {}
"""The entity at the top of each mapped class's chain of extensions (itself, for a root)."""

PROOFS: dict[type, ColumnElement[bool]] = {}
"""Per extension, the condition that says a row of its root has it."""

STORED_IN: dict[type, tuple[Table, Column | None]] = {}
"""Where each extension lives: its table and, when the table is its own, its key column."""

CONSOLIDATED: dict[type, list[tuple[Table, Column, tuple[str, ...], type]]] = {}
"""By consolidated class: each extension's table, its key, the fields of the class it holds and
the extension itself."""


def has_the_row_of(root_table: Table, table: Table) -> ColumnElement[bool]:
    """Whether the root's row has a row in the extension's own table."""
    key = list(table.primary_key.columns)[0]
    root_key = list(root_table.primary_key.columns)[0]
    return exists(select(literal(1)).where(key == root_key).correlate(root_table))


def register(
    entity: type, proof: ColumnElement[bool], table: Table, key: Column | None
) -> None:
    """Notes how a row of the root is recognised as `entity`, and where `entity` is stored."""
    PROOFS[entity] = proof
    STORED_IN[entity] = (table, key)
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
        """`root` is the chain's top entity; `within` the class being read (the root when
        not given) — only it and what extends it are candidates."""
        super().__init__()
        self.root = root
        self.within = within or root

    def _gen_cache_key(self, anon_map, bindparams):  # type: ignore[no-untyped-def]  # noqa: ARG002
        """The statement-cache key: the classes, the root's mode (strict, stored) and the
        identities registered now — so a compiled statement never outlives a new extension."""
        # The statement is compiled from what is registered, so what is registered is part
        # of its identity: a module installed later never meets a stale compiled statement.
        known = tuple(one.identity for one in extensions.chain(self.root, self.root))
        return (
            self.__class__,
            self.root.__qualname__,
            self.within.__qualname__,
            self.root in STRICT,
            self.root in STORED_TYPE,
            known,
        )


def derived(root: Entities, within: Entities | None = None) -> ColumnElement:
    """The type of a row worked out from the rows that exist: what `ExtensionType` is when the
    table stores nothing, and what `reclassify` writes when it does.

        derived(Move)
            ->  CASE WHEN EXISTS (… move_tracking …) THEN 'billing.MoveTracking'
                     WHEN EXISTS (… move_delivery …) THEN 'billing.MoveDelivery'
                     ELSE 'billing.Move' END
        derived(Partner)   ->  'contacts.Partner', a literal: nothing extends it

    The deepest extension is asked first, so a row answers the most derived class it has;
    `within` starts the choice lower down the chain.
    """
    within = within or root
    branches = [
        (PROOFS[one.entity], literal(one.identity, String()))
        for one in extensions.chain(root, within)
    ]
    if not branches:
        return literal(identity_of(within), String())
    return case(*branches, else_=literal(identity_of(within), String()))


@compiles(ExtensionType)
def compile_extension_type(element: ExtensionType, compiler, **kw):  # type: ignore[no-untyped-def]
    """The SQL an `ExtensionType` compiles to, from what is registered at compile time.

    1. A strict root: the identity of the class read, a literal — never an extension.
    2. No stored type column: `derived` — a CASE over each extension's proof.
    3. A stored type column: only the extensions sharing the root's table (no row of their
       own) are worked out; otherwise the stored value, when it names the class read or one
       below it.
    Final: the class read itself, for a value of a class no longer installed.

        select(Move) on a row in move and move_delivery   ->  'MoveDelivery'
    """
    # Reading from an extension decides only among that extension and what extends it: a sibling
    # with a row of its own is another kind of the same record, not a level below this one.
    root, within = element.root, element.within
    if root in STRICT:
        return compiler.process(literal(identity_of(within), String()), **kw)
    stored = STORED_TYPE.get(root)
    if stored is None:
        return compiler.process(derived(root, within), **kw)
    # The table stores the type: only an extension with no row of its own is worked out. A value
    # of a class no longer installed, or outside what is read from, falls back to the level.
    below = extensions.chain(root, within)
    shared = [
        (PROOFS[one.entity], literal(one.identity, String()))
        for one in below
        if STORED_IN[one.entity][1] is None
    ]
    kept = case(
        *shared,
        (stored.in_([identity_of(within), *(one.identity for one in below)]), stored),
        else_=literal(identity_of(within), String()),
    )
    return compiler.process(kept, **kw)
