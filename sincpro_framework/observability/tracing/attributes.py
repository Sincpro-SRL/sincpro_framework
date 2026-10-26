"""`traces`: what a use case says about itself, on its own span (PRD_03 §3.1).

    @siat.app_service(CommandSendDocument)
    @traces.attributes(
        field_path(CommandSendDocument).nit,              # siat.nit, set before execute runs
        field_path(ResponseSendDocument).reception_code,  # siat.reception_code, on success
        namespace="siat",
        # siat.branch — named, not derived
        branch=field_path(CommandSendDocument).branch_office,
    )
    class SendDocument(ApplicationService):
        def execute(self, dto: CommandSendDocument) -> ResponseSendDocument:
            cuf = ...
            traces.annotate({"siat.cuf": cuf})        # known only midway: by hand

Context: a NIT, a merchant, a bank's transaction id are exactly what a trace is filtered and
grouped by ("the rejected invoices of NIT X", TraceQL `count_over_time() by (span.siat.nit)`).
Both doors write on the span the bus opened for the use case (`context/Command`), never on a
child an adapter opened: the declared one reads the Command before `execute` runs, so a failed
run carries it too, and the Response after a successful one. A reference into the context type
(`field_path(BillingContext).channel`) reads the execution context, at the start and again at
the end.

Nothing here can fail a use case, and nothing is refused for its name or its content (PRD_03
§4.10): what a service shows is its decision. A declaration is checked where it is written for
what could never work — a field that does not exist, a value a span cannot hold, a key declared
twice. By hand, a value a span cannot hold is dropped and logged once; without OpenTelemetry, or
outside a use case, both doors do nothing.
"""

import types
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import ConfigDict

from sincpro_framework import exceptions
from sincpro_framework.observability import correlation
from sincpro_framework.observability.field_paths import FieldPath
from sincpro_framework.observability.tracing.span_execution import use_case_span
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

type SpanScalar = str | bool | int | float | Decimal | Enum | UUID | date
type SpanValue = SpanScalar | Sequence[SpanScalar] | None
"""What an attribute may be handed. It travels as OpenTelemetry takes it: an Enum as its value,
a Decimal as a float, a UUID as text, a date as ISO 8601, a sequence as a homogeneous tuple.
`None` is left off the span."""

type AttributeValue = (
    str
    | bool
    | int
    | float
    | tuple[str, ...]
    | tuple[bool, ...]
    | tuple[int, ...]
    | tuple[float, ...]
)

# --- the rule a key follows ---------------------------------------------------------------------


def key_problem(key: str) -> str | None:
    """Why `key` cannot be a span attribute, or `None` when it can: any text that is not empty.
    What a service puts on its spans is its decision (PRD_03 §4.10); a key the framework also
    writes takes the project's value."""
    if not isinstance(key, str) or not key.strip():
        return f"{key!r} is not a key — a span attribute is named by some text"
    return None


# --- the rules a value follows ------------------------------------------------------------------

_SCALARS = (str, bool, int, float, Decimal, UUID, date)
_SEQUENCES = (list, tuple, set, frozenset, Sequence)


def _unwrapped(annotation: Any) -> Any:
    """`Annotated[X, ...]` and `NewType("Id", X)` as `X`."""
    while True:
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        elif hasattr(annotation, "__supertype__"):
            annotation = annotation.__supertype__
        else:
            return annotation


def _scalar_annotation(annotation: Any) -> bool:
    """Whether one annotation is a single value a span holds — a scalar type, an Enum, or a
    `Literal` of scalars; `bytes` never.

        _scalar_annotation(Decimal)              ->  True
        _scalar_annotation(Literal["a", "b"])    ->  True
        _scalar_annotation(bytes)                ->  False
    """
    annotation = _unwrapped(annotation)
    if get_origin(annotation) is Literal:
        return all(isinstance(one, (str, bool, int, float)) for one in get_args(annotation))
    if not isinstance(annotation, type) or issubclass(annotation, bytes):
        return False
    return issubclass(annotation, (Enum, *_SCALARS))


def _members_of(annotation: Any) -> tuple[Any, ...]:
    """The members of a union without `None`, or the annotation alone.

    _members_of(str | int | None)  ->  (str, int)        _members_of(str)  ->  (str,)
    """
    if get_origin(annotation) in (Union, types.UnionType):
        return tuple(one for one in get_args(annotation) if one is not type(None))
    return (annotation,)


def holds_on_a_span(annotation: Any) -> bool:
    """Whether a field annotated `annotation` fits in a span attribute: a scalar, an Enum, a
    Literal, or a sequence of them — optional or a union of them too. A DTO, a mapping, bytes or
    `Any` do not: a span holds values, not documents.

        holds_on_a_span(str | None)           ->  True
        holds_on_a_span(list[Currency])       ->  True
        holds_on_a_span(tuple[int, ...])      ->  True
        holds_on_a_span(dict[str, str])       ->  False
        holds_on_a_span(list)                 ->  False      # its items are not named

    Every member of a union must hold; `Annotated` and `NewType` are read as what they wrap.
    """
    for member in _members_of(_unwrapped(annotation)):
        member = _unwrapped(member)
        origin = get_origin(member)
        if isinstance(origin, type) and issubclass(origin, _SEQUENCES):
            items = [one for one in get_args(member) if one is not Ellipsis]
            if not items or not all(_scalar_annotation(one) for one in items):
                return False
        elif not _scalar_annotation(member):
            return False
    return True


def _scalar(value: Any) -> str | bool | int | float:
    """One value as OpenTelemetry takes it — an Enum as its value, a Decimal as a float, a
    UUID as text, a date (or datetime) as ISO 8601; anything else raises `TypeError`.

    Context: `bool` is checked before `int` — it is one — so `True` stays `True`, not `1`; a
    `str` or `int` subclass travels as the plain type.

        _scalar(Decimal("10.5"))     ->  10.5
        _scalar(date(2026, 1, 31))   ->  "2026-01-31"
    """
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not a value a span holds")


def span_value(value: Any) -> AttributeValue | None:
    """`value` as OpenTelemetry takes it — see `SpanValue`. `None` stays off the span; a value a
    span cannot hold raises `TypeError`.

        span_value(Currency.BOB)       ->  "BOB"
        span_value(["a", None, "b"])   ->  ("a", "b")
        span_value([1, "x"])           ->  ("1", "x")        # mixed types: all as text
        span_value(None)               ->  None
        span_value({"a": 1})           ->  TypeError("dict is not a value a span holds")

    A list, tuple, set or frozenset becomes a tuple, its `None`s dropped; a set preserves the
    order it iterates in."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_scalar(one) for one in value if one is not None]
        if len({type(one) for one in items}) > 1:
            return tuple(str(one) for one in items)
        return tuple(items)  # type: ignore[return-value]
    return _scalar(value)


# --- declared: on the class, read off the run's Command and Response -----------------------------


class SpanAttribute(DataTransferObject):
    """One attribute a use case declared: its key, and the field it is read from."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    key: str
    path: FieldPath


_by_class: "WeakKeyDictionary[type, tuple[SpanAttribute, ...]]" = WeakKeyDictionary()


def declared_attributes(cls: type) -> tuple[SpanAttribute, ...]:
    """What `cls` declared, and what the classes it inherits from declared."""
    found: list[SpanAttribute] = []
    for one in reversed(cls.__mro__):
        found.extend(_by_class.get(one, ()))
    return tuple(found)


def _declared(reference: Any, key: str) -> SpanAttribute:
    """One declared attribute, checked where it is written: `reference` must be a field path,
    `key` some text, and the field a value a span can hold — refused at import otherwise.

        _declared(field_path(CommandSendDocument).nit, "siat.nit")
            ->  SpanAttribute(key="siat.nit", path=field_path(CommandSendDocument).nit)
    """
    path = FieldPath.required(reference, f"span attribute '{key}'")
    problem = key_problem(key)
    if problem is not None:
        raise exceptions.ProgrammingError(f"span attribute {path!r}: {problem}")
    if not holds_on_a_span(path.annotation):
        name = getattr(path.annotation, "__name__", repr(path.annotation))
        raise exceptions.ProgrammingError(
            f"span attribute {path!r}: {path.key} is {name} — a span attribute is a str, bool, "
            "int, float, Decimal, Enum, UUID, date or a sequence of them"
        )
    return SpanAttribute(key=key, path=path)


def _declaring(attributes: tuple[SpanAttribute, ...]):  # type: ignore[no-untyped-def]
    """The class decorator `traces.attributes` answers.

    1. Every path must start at the use case's Command, Response or declared context type.
    2. A key the class — or what it inherits — already declared is refused.
    Final: the attributes stored for the class, after the ones it had; the class unchanged.
    """

    def declared[T: type](cls: T) -> T:
        FieldPath.refuse_foreign(cls, tuple(one.path for one in attributes))
        known = {one.key for one in declared_attributes(cls)}
        for one in attributes:
            if one.key in known:
                raise exceptions.ProgrammingError(
                    f"{cls.__name__}: span attribute '{one.key}' declared twice"
                )
            known.add(one.key)
        _by_class[cls] = (*_by_class.get(cls, ()), *attributes)
        return cls

    return declared


# --- by hand, on the span of the use case running now ---------------------------------------------

_warned: set[str] = set()


def _warn_once(key: str, problem: str) -> None:
    """Log `problem` the first time `key` has one — a use case that runs a million times
    must not log a million lines. Never raises."""
    if key in _warned:
        return
    _warned.add(key)
    try:
        logger.warning(f"traces: span attribute dropped — {problem}")
    except Exception:
        pass


def _set(span: Any, key: str, value: Any) -> None:
    """Set one attribute on `span`, converted (`span_value`); `None` is left off, a value a
    span cannot hold dropped and warned about once."""
    try:
        converted = span_value(value)
    except TypeError as error:
        _warn_once(key, f"'{key}': {error}")
        return
    if converted is not None:
        span.set_attribute(key, converted)


def describe(span: Any, use_case: type, source: Any) -> None:
    """The attributes `use_case` declared that start at `source` — its Command before it runs,
    its Response after a success. Never raises; with no span, costs one check.

        describe(span, SendDocument, CommandSendDocument(nit="1234", branch_office=0))
            ->  span: siat.nit="1234", siat.branch=0
        describe(span, SendDocument, ResponseSendDocument(reception_code="R-9"))
            ->  span: siat.reception_code="R-9"
        describe(None, SendDocument, command)   ->  nothing

    A context path is read off the execution context on every call, whatever `source` is;
    an error on the way is warned about once per use case."""
    if span is None:
        return
    try:
        for one in declared_attributes(use_case):
            if one.path.reads_context:
                _set(span, one.key, one.path.read(correlation.execution_context()))
            elif source is not None and isinstance(source, one.path.root):
                _set(span, one.key, one.path.read(source))
    except Exception as error:
        _warn_once(f"{use_case.__name__}:describe", f"{use_case.__name__}: {error}")


# --- the door ------------------------------------------------------------------------------------


class Traces:
    """What a use case puts on its own span — declared on the class, or by hand in `execute`."""

    def attributes(  # type: ignore[no-untyped-def]
        self, *references: Any, namespace: str, **named: Any
    ):
        """Attributes of the use case's span, read off its Command (before `execute`, so a
        failed run has them) and its Response (after a success). A reference
        `field_path(X).a.b` is `{namespace}.a.b`; a named one, `{namespace}.{name}`.

            traces.attributes(field_path(CommandSendDocument).nit, namespace="siat",
                              branch=field_path(CommandSendDocument).branch_office)
                ->  a class decorator declaring "siat.nit" and "siat.branch"
            traces.attributes(namespace="siat")
                ->  ProgrammingError, none declared
            traces.attributes(field_path(X).nit, namespace="")
                ->  ProgrammingError, no namespace

        Each attribute is checked as it is written (`_declared`); the decorator then checks
        them against the use case it decorates (`_declaring`)."""
        if not isinstance(namespace, str) or not namespace.strip():
            raise exceptions.ProgrammingError(
                f"span attributes: namespace {namespace!r} — the prefix every key takes: "
                "'siat', 'payment'"
            )
        declared = [
            _declared(
                one,
                f"{namespace}.{'.'.join(FieldPath.required(one, 'a span attribute').names)}",
            )
            for one in references
        ]
        declared += [_declared(one, f"{namespace}.{name}") for name, one in named.items()]
        if not declared:
            raise exceptions.ProgrammingError(
                "span attributes: nothing declared — hand at least one field_path(X).field"
            )
        return _declaring(tuple(declared))

    def annotate(self, attributes: Mapping[str, SpanValue]) -> None:
        """Attributes on the span of the use case running now — for a value known only midway
        (a code the provider answered, an id computed in `execute`).

        Context: never raises. A key that breaks the rules, or a value a span cannot hold, is
        dropped and logged once; outside a use case, or with tracing off, nothing happens.

            traces.annotate({"siat.cuf": cuf, "siat.retries": 2})
                ->  both on the use case's span
            traces.annotate({"": "x", "siat.raw": b"..."})
                ->  neither: an empty key and bytes, each warned about once

        1. The attributes as `(key, value)` pairs — not a mapping: nothing happens.
        2. The span of the use case running now (`use_case_span`).
        3. For each pair:
           3.1 a key that is not some text is warned about once and skipped, span or not;
           3.2 no span: skipped;
           3.3 the value converted and set (`_set`) — `None` left off, a value a span cannot
               hold dropped and warned about once; a span that raises, warned about once.
        Final: nothing returned, nothing raised.
        """
        try:
            items = list(attributes.items())
        except Exception:
            return
        span = use_case_span()
        for key, value in items:
            problem = key_problem(key)
            if problem is not None:
                _warn_once(str(key), problem)
                continue
            if span is None:
                continue
            try:
                _set(span, key, value)
            except Exception as error:
                _warn_once(key, f"'{key}': {error}")


traces = Traces()
