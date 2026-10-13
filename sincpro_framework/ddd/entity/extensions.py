"""What extends an entity: which classes add fields to it, and who owns each field.

    Move ← MoveDelivery(route) ← MoveTracking(carrier)       a chain of modules
    Move ← MoveFiscal(cufd)                                  a sibling

The data layer registers an extension when it maps it (`map_aggregates`); everything that has to
know whose a field is asks here — `Meta` (the owner of each field), the answer's schema and its
shape (flat, or each extension under its own key), `Edit` (which class a record is created as),
and the filters (which table a field lives in). Nothing here knows how the fields are stored.
"""

import re
from dataclasses import dataclass
from typing import Any

from sincpro_framework.exceptions import ClientError


def identity_of(entity: type) -> str:
    """How a class is named wherever a row says what it is: its module and its qualified name.

    The mapper's `polymorphic_identity`, the literal a statement compares and the value a core
    stores in its `extension_type` column must be the same text, so the rule is written once. Two
    modules that both define a `MoveDelivery` are two identities.
    """
    return f"{entity.__module__}.{entity.__qualname__}"


@dataclass(frozen=True)
class EntityExtension:
    entity: type
    """The class that adds the fields."""
    root: type
    """The entity at the top of its chain: the one whose reads answer it."""
    fields: tuple[str, ...]
    """The fields it adds: those of its own, not the ones it inherits."""
    key: str
    """Where a delegated answer nests it: `MoveDelivery` under `Move` is `delivery`."""
    identity: str
    """What a row of this class is called (`identity_of`)."""
    depth: int
    """How far down its chain it stands; the deepest is asked first."""


EXTENSIONS: dict[type, list[EntityExtension]] = {}
"""By root, in the order they were registered."""

OVERLAYABLE = ("label", "help", "readonly", "required")
"""What a module may say differently about a field of the core: how it is shown, never what it is."""

OVERLAYS: dict[type, dict[str, dict[str, Any]]] = {}
"""By entity, field by field, what modules changed about how a field is shown."""

DELEGATED: set[type] = set()
"""Roots whose answers nest each extension under its key, instead of writing its fields flat."""


def key_of(root: type, entity: type) -> str:
    """`MoveDelivery` under `Move` → `delivery`; a name that does not start with the root's is
    written whole, in snake case."""
    name = entity.__name__
    if name.startswith(root.__name__) and name != root.__name__:
        name = name[len(root.__name__) :]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def register(root: type, entity: type, fields: tuple[str, ...]) -> EntityExtension:
    found = EntityExtension(
        entity, root, fields, key_of(root, entity), identity_of(entity), len(entity.__mro__)
    )
    registered = EXTENSIONS.setdefault(root, [])
    registered[:] = [one for one in registered if one.entity is not entity]
    registered.append(found)
    return found


def under(entity: type) -> list[EntityExtension]:
    """The extensions that add to `entity` or to what it extends: every one below it, not itself.

    under(Move)          →  [MoveDelivery, MoveTracking, MoveFiscal]
    under(MoveDelivery)  →  [MoveTracking]
    """
    return [
        one
        for registered in EXTENSIONS.values()
        for one in registered
        if one.entity is not entity and issubclass(one.entity, entity)
    ]


def chain(root: type, within: type) -> list[EntityExtension]:
    """The extensions of `root` that are `within` or extend it, the deepest first: the ones a read
    starting at `within` chooses among."""
    return sorted(
        (one for one in EXTENSIONS.get(root, []) if issubclass(one.entity, within)),
        key=lambda one: -one.depth,
    )


def owner_of(entity: type, field: str) -> EntityExtension | None:
    """The extension below `entity` that adds `field`; `None` when it is the entity's own."""
    return next((one for one in under(entity) if field in one.fields), None)


def class_for(entity: type, names: set[str]) -> type:
    """The class a record is created as when it carries these fields: `entity` itself, or the
    deepest extension of it that holds them all.

        class_for(Move, {"number"})            →  Move
        class_for(Move, {"number", "route"})   →  MoveDelivery
        class_for(Move, {"route", "carrier"})  →  MoveTracking          one chain holds both
        class_for(Move, {"route", "cufd"})     →  refused               siblings: no one class
    """
    needed = [one for name in names if (one := owner_of(entity, name)) is not None]
    if not needed:
        return entity
    deepest = max(needed, key=lambda one: len(one.entity.__mro__))
    apart = sorted(
        {one.entity.__name__ for one in needed if not issubclass(deepest.entity, one.entity)}
    )
    if apart:
        raise ClientError(
            f"{', '.join(sorted(names & {f for one in needed for f in one.fields}))} belong to "
            f"{deepest.entity.__name__} and {', '.join(apart)}, which no one class holds: "
            "send them in separate edits, or map a Consolidated record for the screen that "
            "needs them together"
        )
    return deepest.entity


def overlay(entity: type, field: str, changes: dict[str, Any]) -> None:
    """A module's say on how a field of `entity` is shown: its label, its help, whether a form
    asks for it or shows it read-only. Hints, like the ones the class declares; the last module to
    say a thing wins. Anything else about a field belongs to the core and is refused."""
    unknown = sorted(set(changes) - set(OVERLAYABLE))
    if unknown:
        raise ClientError(
            f"a module may change how {entity.__name__}.{field} is shown ({', '.join(OVERLAYABLE)}), "
            f"not {', '.join(unknown)}: that is the core's"
        )
    OVERLAYS.setdefault(entity, {}).setdefault(field, {}).update(changes)
