"""`of(Command).field`: a reference to a field, written as code, never as a string.

    of(CommandIssueInvoice).currency        # type-checks as the field's type; renaming the field
                                            # renames this; a field it lacks raises right here
    of(ResponseIssueInvoice).customer.segment   # nested DTOs too
    of(BillingContext)["company"]           # a key of the execution context, typed as a TypedDict:
                                            # read as the context is, and checked as its keys are

Context: a metric labelled `"currency"` drifts from the DTO the first time somebody renames the
field — the dashboard goes silently empty. `of()` hands back a stand-in typed as an instance of
the class, so the type checker and the editor see an ordinary attribute access; at run time the
stand-in records the path and checks every step against the class's declared fields. The DTO is
never touched: no metaclass, no descriptor, nothing on the class.
"""

import dataclasses
import types
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
    overload,
)

from pydantic import BaseModel


def refused(message: str) -> Exception:
    """Context: imported here, not at the top — the bus measures every run through this
    package, and a service that only runs buses must not load the DDD layer for it."""
    from sincpro_framework.ddd.exceptions import ContractViolation

    return ContractViolation(message)


def is_context_type(cls: Any) -> bool:
    """Whether `cls` types an execution context — a `TypedDict`, as `Feature[..., ContextT]`
    and `Hook[ContextT]` take it. A path from one reads the context, not a DTO."""
    try:
        return is_typeddict(cls)
    except Exception:
        return False


def _declared_fields(cls: type) -> dict[str, Any] | None:
    """A class's fields and their annotations — a pydantic model, a dataclass or a TypedDict;
    `None` for a class that declares none, which a path cannot step into."""
    if is_context_type(cls):
        try:
            return dict(get_type_hints(cls))
        except Exception:
            return dict(getattr(cls, "__annotations__", {}))
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

    def __getitem__(self, name: str) -> "FieldPath":
        """`of(BillingContext)["company"]` — a context type is a TypedDict, read by key; the type
        checker knows its keys, so a key it lacks is flagged where it is written."""
        if not isinstance(name, str):
            raise refused(f"{self!r}[{name!r}]: a key of the context is some text")
        return self.__getattr__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a field reference is read-only")

    def __repr__(self) -> str:
        if is_context_type(self._root):
            return f"of({self._root.__name__})" + "".join(f"[{one!r}]" for one in self._names)
        return f"of({self._root.__name__}).{'.'.join(self._names)}"

    @property
    def reads_context(self) -> bool:
        """Whether this path starts at a context type, and so reads the execution context."""
        return is_context_type(self._root)

    def read(self, source: Any) -> Any:
        """The value on `source`, walking the path — `None` where a step is `None`. A mapping
        (the execution context) is read by key."""
        value = source
        for name in self._names:
            if value is None:
                return None
            value = value.get(name) if isinstance(value, Mapping) else getattr(value, name)
        return value

    def read_context(self) -> Any:
        """The value in the execution in progress's context, as it is now."""
        from sincpro_framework.observability.correlation import execution_context

        return self.read(execution_context())


type ContextKeys = Mapping[str, Any]
"""What `of(ContextType)` is typed as: a mapping read by key. A context type is a `TypedDict`,
usually `total=False`, whose keys a type checker would call possibly absent; the key is checked
where it is written, at import, instead."""


@overload
def of(cls: type[Mapping[str, Any]]) -> ContextKeys: ...


@overload
def of[T](cls: type[T]) -> T: ...


def of(cls: Any) -> Any:
    """A reference to `cls`'s fields — see the module. Typed as `T` on purpose: that is what
    lets the type checker and a rename follow `of(Command).field`. A context type is read by
    key, `of(BillingContext)["company"]`, as the context itself is."""
    if _declared_fields(cls) is None:
        raise refused(
            f"of({getattr(cls, '__name__', cls)!r}): a field reference starts at a "
            "DataTransferObject, a dataclass or a context TypedDict"
        )
    return FieldPath(cls, (), cls)


def field_path(reference: Any, role: str) -> FieldPath:
    """`reference` as the path it must be — refused when a value was handed instead."""
    if not isinstance(reference, FieldPath) or not reference.names:
        raise refused(
            f"{role} is a field reference — of(Command).field — never a value "
            f"({reference!r} given)"
        )
    return reference


_unbounded_warned: set[str] = set()


def bounded_label(path: FieldPath) -> FieldPath:
    """A label is anything the project chooses; one that is not an `Enum`, a `Literal` or a
    `bool` is accepted with one warning.

    Context: every distinct value is one more series in the backend for as long as it lives —
    worth saying where the label is declared. What a service measures by is its decision
    (PRD_03 §4.10), so it is never refused."""
    annotation = without_none(path.annotation)
    if annotation is bool or get_origin(annotation) is Literal:
        return path
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return path
    where = repr(path)
    if where not in _unbounded_warned:
        _unbounded_warned.add(where)
        name = getattr(annotation, "__name__", repr(annotation))
        try:
            from sincpro_framework.sincpro_logger import logger

            logger.warning(
                f"metrics: label {where} is {name}, not an Enum, a Literal or a bool — each "
                "distinct value is one more series in the backend"
            )
        except Exception:
            pass
    return path


def _members(annotation: Any) -> tuple[type, ...]:
    if get_origin(annotation) in (Union, types.UnionType):
        return tuple(one for one in get_args(annotation) if isinstance(one, type))
    return (annotation,) if isinstance(annotation, type) else ()


def context_types_of(cls: type) -> tuple[type, ...]:
    """The context types a handler declares — the `ContextT` of `Feature[C, R, ContextT]`,
    `ApplicationService[C, R, ContextT]` or `Hook[ContextT]`, anywhere in what it inherits."""
    found: list[type] = []
    for one in getattr(cls, "__mro__", ()):
        for base in getattr(one, "__orig_bases__", ()):
            for argument in get_args(base):
                if is_context_type(argument) and argument not in found:
                    found.append(argument)
    return tuple(found)


def _refuse_foreign_context(cls: type, paths: tuple[FieldPath, ...]) -> None:
    """A context path starts at the context type the handler declares; unchecked when it
    declares none."""
    declared = context_types_of(cls)
    if not declared:
        return
    for path in paths:
        if path.reads_context and path.root not in declared:
            raise refused(
                f"{cls.__name__}: {path!r} — {path.root.__name__} is not the context "
                f"{cls.__name__} declares ({', '.join(one.__name__ for one in declared)})"
            )


def refuse_foreign_paths(cls: type, paths: tuple[FieldPath, ...]) -> None:
    """A path starts at the use case's Command, its Response, or the context type it declares —
    read off `execute`'s annotations and its generic parameters; unchecked when it has none.
    Shared by everything that reads a run off a declaration: the metrics and the span
    attributes."""
    _refuse_foreign_context(cls, paths)
    paths = tuple(one for one in paths if not one.reads_context)
    if not paths:
        return
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
