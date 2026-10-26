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
never touched: no metaclass, no descriptor, nothing on the class. A path reads whatever it is
handed — the execution context is handed in by whoever reads it, never fetched from here.
"""

import dataclasses
import types
from collections.abc import Mapping
from decimal import Decimal
from typing import (
    Any,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
    overload,
)

from pydantic import BaseModel

from sincpro_framework import exceptions


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
    """`X | None` as `X` — an optional label or value is still bounded, or still a number.

        without_none(str | None)             ->  str
        without_none(Optional[Currency])     ->  Currency
        without_none(int | str | None)       ->  int | str | None      # two left: unchanged

    Only a union that leaves exactly one type is narrowed; anything else comes back as it is.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        members = [one for one in get_args(annotation) if one is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _members(annotation: Any) -> tuple[type, ...]:
    """The classes an annotation admits — every class of a union, the class itself, or none.

    _members(CommandA | CommandB)  ->  (CommandA, CommandB)      _members("x")  ->  ()
    """
    if get_origin(annotation) in (Union, types.UnionType):
        return tuple(one for one in get_args(annotation) if isinstance(one, type))
    return (annotation,) if isinstance(annotation, type) else ()


def _context_types_of(cls: type) -> tuple[type, ...]:
    """The context types a handler declares — the `ContextT` of `Feature[C, R, ContextT]`,
    `ApplicationService[C, R, ContextT]` or `Hook[ContextT]`, anywhere in what it inherits.

        class IssueInvoice(Feature[CommandIssueInvoice, ResponseIssueInvoice, BillingContext])
        _context_types_of(IssueInvoice)   ->  (BillingContext,)
        _context_types_of(Untyped)        ->  ()                 # a Feature with no ContextT

    Every generic base along the MRO is read; each context type once, in the order found.
    """
    found: list[type] = []
    for one in getattr(cls, "__mro__", ()):
        for base in getattr(one, "__orig_bases__", ()):
            for argument in get_args(base):
                if is_context_type(argument) and argument not in found:
                    found.append(argument)
    return tuple(found)


def _refuse_foreign_context(cls: type, paths: tuple["FieldPath", ...]) -> None:
    """A context path starts at the context type the handler declares; unchecked when it
    declares none."""
    declared = _context_types_of(cls)
    if not declared:
        return
    for path in paths:
        if path.reads_context and path.root not in declared:
            raise exceptions.ProgrammingError(
                f"{cls.__name__}: {path!r} — {path.root.__name__} is not the context "
                f"{cls.__name__} declares ({', '.join(one.__name__ for one in declared)})"
            )


NUMBERS = (int, float, Decimal)


class FieldPath:
    """Where a value lives on a DTO: its class, the attributes to walk, what the last one is."""

    __slots__ = ("_root", "_names", "_annotation")

    def __init__(self, root: type, names: tuple[str, ...], annotation: Any) -> None:
        """Set the three slots past `__setattr__`, which refuses every write — a path is
        immutable once built."""
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_names", names)
        object.__setattr__(self, "_annotation", annotation)

    @property
    def root(self) -> type:
        """The class the path starts at — the Command, the Response or a context type."""
        return self._root

    @property
    def names(self) -> tuple[str, ...]:
        """The attributes walked from the root, in order — `("customer", "segment")`."""
        return self._names

    @property
    def annotation(self) -> Any:
        """The declared type of the last step — it tells a number from a bounded label."""
        return self._annotation

    @property
    def key(self) -> str:
        """The label key this path answers under: its last attribute."""
        return self._names[-1] if self._names else ""

    def __getattr__(self, name: str) -> "FieldPath":
        """One step further — `of(Command).customer` → `of(Command).customer.segment`.

        Context: this is where a renamed or mistyped field fails, at import, instead of a
        dashboard going silently empty.

        1. A private name is a plain `AttributeError` — copy, pickle and friends probe them.
        2. The current step must be a DTO (a model, a dataclass, a TypedDict), `X | None`
           read as `X`.
        3. It must declare `name`; the error lists the fields it does have.
        Final: a new path, one name longer, typed as that field.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        current = without_none(self._annotation)
        fields = _declared_fields(current)
        where = ".".join((getattr(self._root, "__name__", "?"), *self._names))
        if fields is None:
            raise exceptions.ProgrammingError(
                f"{where} is not a DTO: there is no field '{name}' to read"
            )
        if name not in fields:
            raise exceptions.ProgrammingError(
                f"{where} has no field '{name}' — it has {', '.join(sorted(fields)) or 'none'}"
            )
        return FieldPath(self._root, (*self._names, name), fields[name])

    def __getitem__(self, name: str) -> "FieldPath":
        """`of(BillingContext)["company"]` — a context type is a TypedDict, read by key; the type
        checker knows its keys, so a key it lacks is flagged where it is written."""
        if not isinstance(name, str):
            raise exceptions.ProgrammingError(
                f"{self!r}[{name!r}]: a key of the context is some text"
            )
        return self.__getattr__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Refused — a field reference is read-only."""
        raise AttributeError("a field reference is read-only")

    def __repr__(self) -> str:
        """The path as it was written — what a warning or a refusal quotes.

        of(CommandIssueInvoice).customer.segment
        of(BillingContext)['company']
        """
        if is_context_type(self._root):
            return f"of({self._root.__name__})" + "".join(f"[{one!r}]" for one in self._names)
        return f"of({self._root.__name__}).{'.'.join(self._names)}"

    @property
    def reads_context(self) -> bool:
        """Whether this path starts at a context type, and so reads the execution context."""
        return is_context_type(self._root)

    def read(self, source: Any) -> Any:
        """The value on `source`, walking the path — `None` where a step is `None`. A mapping
        (the execution context) is read by key.

            of(CommandIssueInvoice).customer.segment.read(command)    ->  "retail"
            of(CommandIssueInvoice).customer.segment.read(no_customer)  ->  None
            of(BillingContext)["company"].read({"company": 3})        ->  3
            of(BillingContext)["company"].read({})                    ->  None

        An attribute the object lacks raises `AttributeError`: the path was checked against
        the class, so that is an object of another class."""
        value = source
        for name in self._names:
            if value is None:
                return None
            value = value.get(name) if isinstance(value, Mapping) else getattr(value, name)
        return value

    def numeric(self) -> "FieldPath":
        """This path, when what it reads is a number — what is summed or measured; refused
        otherwise.

            of(ResponseIssueInvoice).total.numeric()      ->  the path      # a Decimal
            of(CommandIssueInvoice).currency.numeric()
                ->  ProgrammingError("of(CommandIssueInvoice).currency: currency is not a
                    number — only a number is summed or measured")

        A `bool` is an `int` to Python, never a number here."""
        annotation = without_none(self._annotation)
        if (
            isinstance(annotation, type)
            and issubclass(annotation, NUMBERS)
            and annotation is not bool
        ):
            return self
        raise exceptions.ProgrammingError(
            f"{self!r}: {self.key} is not a number — only a number is summed or measured"
        )

    @classmethod
    def required(cls, reference: Any, role: str) -> "FieldPath":
        """`reference` as the path it must be — refused when a value was handed instead.

        FieldPath.required(of(CommandIssueInvoice).currency, "a label")   ->  the path
        FieldPath.required("currency", "a label")
            ->  ProgrammingError("a label is a field reference — of(Command).field — never
                a value ('currency' given)")
        """
        if not isinstance(reference, cls) or not reference.names:
            raise exceptions.ProgrammingError(
                f"{role} is a field reference — of(Command).field — never a value "
                f"({reference!r} given)"
            )
        return reference

    @classmethod
    def refuse_foreign(cls, handler: type, paths: tuple["FieldPath", ...]) -> None:
        """A path starts at the use case's Command, its Response, or the context type it
        declares — read off `execute`'s annotations and its generic parameters; unchecked when
        it has none. Shared by everything that reads a run off a declaration: the metrics and
        the span attributes.

            FieldPath.refuse_foreign(IssueInvoice, (of(CommandIssueInvoice).currency,))
                ->  None
            FieldPath.refuse_foreign(IssueInvoice, (of(CommandRefund).amount,))
                ->  ProgrammingError("IssueInvoice: of(CommandRefund).amount — CommandRefund is
                    neither the Command nor the Response of IssueInvoice (CommandIssueInvoice,
                    ResponseIssueInvoice)")

        1. A context path must start at a context type the handler declares
           (`_context_types_of`); unchecked when it declares none.
        2. The DTO paths are left; none, and there is nothing more to check.
        3. The classes they may start at, read off `execute`'s annotations:
           3.1 hints that cannot be read, or no annotated parameter: unchecked;
           3.2 the first parameter's class (each of a union) and the return's — none:
               unchecked.
        4. A path whose root is neither one of those classes nor a base of one is refused.
        Final: `None` — every path starts where it may.
        """
        _refuse_foreign_context(handler, paths)
        paths = tuple(one for one in paths if not one.reads_context)
        if not paths:
            return
        try:
            hints = get_type_hints(handler.execute)  # type: ignore[attr-defined]
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
                raise exceptions.ProgrammingError(
                    f"{handler.__name__}: {path!r} — {path.root.__name__} is neither the "
                    f"Command nor the Response of {handler.__name__} "
                    f"({', '.join(one.__name__ for one in known)})"
                )


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
        raise exceptions.ProgrammingError(
            f"of({getattr(cls, '__name__', cls)!r}): a field reference starts at a "
            "DataTransferObject, a dataclass or a context TypedDict"
        )
    return FieldPath(cls, (), cls)
