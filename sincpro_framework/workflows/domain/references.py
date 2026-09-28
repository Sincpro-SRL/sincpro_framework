"""References: how a value names another — `$input.<field>`, `$steps.<id>.<field>`, `$item.<field>`.

Context: a reference is the whole of a string value, never part of one, so it can be checked
against the schemas of the steps before anything runs, and resolving it never guesses: a
reference to something that is not there is an error, never `None`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reference:
    root: str
    path: tuple[str, ...]
    text: str

    @property
    def step(self) -> str | None:
        """The step a `$steps.<id>…` reference names."""
        return self.path[0] if self.root == "steps" and self.path else None

    @property
    def fields(self) -> tuple[str, ...]:
        """The path inside what the root answers — after the step id for `$steps`."""
        return self.path[1:] if self.root == "steps" else self.path


class UnresolvedReference(Exception):
    pass


def reference_in(value: Any) -> Reference | None:
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    root, *path = value[1:].split(".")
    return Reference(root, tuple(path), value)


def references_in(value: Any) -> list[Reference]:
    """Every reference in a literal, a mapping or a list of them."""
    if isinstance(value, Mapping):
        return [one for item in value.values() for one in references_in(item)]
    if isinstance(value, list):
        return [one for item in value for one in references_in(item)]
    found = reference_in(value)
    return [found] if found else []


def resolve(reference: Reference, scope: Mapping[str, Any]) -> Any:
    """What `reference` names in `scope` — `{"input": …, "steps": …, "item": …}`."""
    if reference.root not in scope:
        raise UnresolvedReference(f"{reference.text}: there is no ${reference.root} here")
    current = scope[reference.root]
    walked = f"${reference.root}"
    for part in reference.path:
        if not isinstance(current, Mapping) or part not in current:
            has = (
                ", ".join(current) if isinstance(current, Mapping) else type(current).__name__
            )
            raise UnresolvedReference(
                f"{reference.text}: {walked} has no {part} — it has {has}"
            )
        current = current[part]
        walked = f"{walked}.{part}"
    return current


def resolve_all(value: Any, scope: Mapping[str, Any]) -> Any:
    """`value` with every reference in it replaced by what it names."""
    if isinstance(value, Mapping):
        return {key: resolve_all(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_all(item, scope) for item in value]
    found = reference_in(value)
    return resolve(found, scope) if found else value


def condition_fields(condition: Mapping[str, Any]) -> list[str]:
    """Every `field` of a condition in the grammar of `Criteria` — its references."""
    if "field" in condition:
        return [str(condition["field"])]
    if "negate" in condition:
        return condition_fields(condition["negate"])
    parts = [*condition.get("all", []), *condition.get("any", [])]
    return [one for part in parts for one in condition_fields(part)]


def condition_values(condition: Mapping[str, Any]) -> list[Any]:
    """Every `value` of a condition — each a literal."""
    if "field" in condition:
        return [condition.get("value")]
    if "negate" in condition:
        return condition_values(condition["negate"])
    parts = [*condition.get("all", []), *condition.get("any", [])]
    return [one for part in parts for one in condition_values(part)]
