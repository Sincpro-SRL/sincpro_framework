"""`of(Command).field`: a reference to a field, written as code, never as a string.

    of(CommandIssueInvoice).currency        # type-checks as the field's type; renaming the field
                                            # renames this; a field it lacks raises right here
    of(ResponseIssueInvoice).customer.segment   # nested DTOs too

Context: a metric labelled `"currency"` drifts from the DTO the first time somebody renames the
field — the dashboard goes silently empty. `of()` hands back a stand-in typed as an instance of
the class, so the type checker and the editor see an ordinary attribute access; at run time the
stand-in records the path and checks every step against the class's declared fields. The DTO is
never touched: no metaclass, no descriptor, nothing on the class.
"""

import dataclasses
import types
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel


def refused(message: str) -> Exception:
    """Context: imported here, not at the top — the bus measures every run through this
    package, and a service that only runs buses must not load the DDD layer for it."""
    from sincpro_framework.ddd.exceptions import ContractViolation

    return ContractViolation(message)


def _declared_fields(cls: type) -> dict[str, Any] | None:
    """A class's fields and their annotations — a pydantic model or a dataclass; `None` for a
    class that declares none, which a path cannot step into."""
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return {name: info.annotation for name, info in cls.model_fields.items()}
    if dataclasses.is_dataclass(cls):
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = {}
        return {one.name: hints.get(one.name, one.type) for one in dataclasses.fields(cls)}
    return None


def without_none(annotation: Any) -> Any:
    """`X | None` as `X` — an optional label or value is still bounded, or still a number."""
    if get_origin(annotation) in (Union, types.UnionType):
        kept = [one for one in get_args(annotation) if one is not type(None)]
        if len(kept) == 1:
            return kept[0]
    return annotation


class FieldPath:
    """Where a value lives on a DTO: its class, the attributes to walk, what the last one is."""

    __slots__ = ("_root", "_names", "_annotation")

    def __init__(self, root: type, names: tuple[str, ...], annotation: Any) -> None:
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_names", names)
        object.__setattr__(self, "_annotation", annotation)

    @property
    def root(self) -> type:
        return self._root

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def annotation(self) -> Any:
        return self._annotation

    @property
    def key(self) -> str:
        """The label key this path answers under: its last attribute."""
        return self._names[-1] if self._names else ""

    def __getattr__(self, name: str) -> "FieldPath":
        if name.startswith("_"):
            raise AttributeError(name)
        current = without_none(self._annotation)
        fields = _declared_fields(current)
        where = ".".join((getattr(self._root, "__name__", "?"), *self._names))
        if fields is None:
            raise refused(f"{where} is not a DTO: there is no field '{name}' to read")
        if name not in fields:
            raise refused(
                f"{where} has no field '{name}' — it has {', '.join(sorted(fields)) or 'none'}"
            )
        return FieldPath(self._root, (*self._names, name), fields[name])

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a field reference is read-only")

    def __repr__(self) -> str:
        return f"of({self._root.__name__}).{'.'.join(self._names)}"

    def read(self, source: Any) -> Any:
        """The value on `source`, walking the path — `None` where a step is `None`."""
        value = source
        for name in self._names:
            if value is None:
                return None
            value = getattr(value, name)
        return value


def of[T](cls: type[T]) -> T:
    """A reference to `cls`'s fields — see the module. Typed as `T` on purpose: that is what
    lets the type checker and a rename follow `of(Command).field`."""
    if _declared_fields(cls) is None:
        raise refused(
            f"of({getattr(cls, '__name__', cls)!r}): a field reference starts at a "
            "DataTransferObject or a dataclass"
        )
    return cast(T, FieldPath(cls, (), cls))


def field_path(reference: Any, role: str) -> FieldPath:
    """`reference` as the path it must be — refused when a value was handed instead."""
    if not isinstance(reference, FieldPath) or not reference.names:
        raise refused(
            f"{role} is a field reference — of(Command).field — never a value "
            f"({reference!r} given)"
        )
    return reference


def bounded_label(path: FieldPath) -> FieldPath:
    """A label must take few values: an `Enum`, a `Literal` or a `bool`.

    Context: every distinct value is one more series in the backend, forever. A customer id, an
    amount or free text as a label is the one mistake that takes Prometheus down — refused where
    it is declared, not discovered in production."""
    annotation = without_none(path.annotation)
    if annotation is bool or get_origin(annotation) is Literal:
        return path
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return path
    name = getattr(annotation, "__name__", repr(annotation))
    raise refused(
        f"label {path!r}: {path.key} is {name} — a label is an Enum, a Literal or a bool, so it "
        "takes a bounded set of values; one series per distinct value takes the backend down"
    )


def _members(annotation: Any) -> tuple[type, ...]:
    if get_origin(annotation) in (Union, types.UnionType):
        return tuple(one for one in get_args(annotation) if isinstance(one, type))
    return (annotation,) if isinstance(annotation, type) else ()


def refuse_foreign_paths(cls: type, paths: tuple[FieldPath, ...]) -> None:
    """A path starts at the use case's Command or its Response — read off `execute`'s
    annotations; unchecked when it has none. Shared by everything that reads a run's DTOs off a
    declaration: the metrics and the span attributes."""
    try:
        hints = get_type_hints(cls.execute)  # type: ignore[attr-defined]
    except Exception:
        return
    parameters = [name for name in hints if name != "return"]
    if not parameters:
        return
    known = (*_members(hints[parameters[0]]), *_members(hints.get("return")))
    if not known:
        return
    for path in paths:
        if not any(issubclass(one, path.root) or one is path.root for one in known):
            raise refused(
                f"{cls.__name__}: {path!r} — {path.root.__name__} is neither the Command nor "
                f"the Response of {cls.__name__} ({', '.join(one.__name__ for one in known)})"
            )


NUMBERS = (int, float, Decimal)


def numeric_value(path: FieldPath) -> FieldPath:
    """What is summed or measured is a number."""
    annotation = without_none(path.annotation)
    if (
        isinstance(annotation, type)
        and issubclass(annotation, NUMBERS)
        and annotation is not bool
    ):
        return path
    raise refused(
        f"{path!r}: {path.key} is not a number — only a number is summed or measured"
    )


def label_value(value: Any) -> str:
    """How a label value travels: an Enum's value, `true`/`false`, `none`, the text."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


__all__ = [
    "FieldPath",
    "bounded_label",
    "field_path",
    "label_value",
    "numeric_value",
    "of",
    "refuse_foreign_paths",
    "without_none",
]
