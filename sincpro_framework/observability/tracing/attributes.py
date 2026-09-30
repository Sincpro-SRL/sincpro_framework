"""`traces`: what a use case says about itself, on its own span (PRD_03 §3.1).

    @siat.app_service(CommandSendDocument)
    @traces.attributes(
        of(CommandSendDocument).nit,                  # siat.nit, set before execute runs
        of(ResponseSendDocument).reception_code,      # siat.reception_code, on success
        namespace="siat",
        branch=of(CommandSendDocument).branch_office,  # siat.branch — named, not derived
    )
    class SendDocument(ApplicationService):
        def execute(self, dto: CommandSendDocument) -> ResponseSendDocument:
            cuf = ...
            traces.annotate({"siat.cuf": cuf})        # known only midway: by hand

Context: a NIT, a merchant, a bank's transaction id vary without bound — a metric refuses them as
labels (§4.4), yet they are exactly what a trace is filtered and grouped by ("the rejected
invoices of NIT X", TraceQL `count_over_time() by (span.siat.nit)`). Both doors write on the span
the bus opened for the use case (`context/Command`), never on a child an adapter opened: the
declared one reads the Command before `execute` runs, so a failed run carries it too, and the
Response after a successful one.

Nothing here can fail a use case. A declaration is checked where it is written — a field that
does not exist, a value a span cannot hold, a key that is not namespaced, one that would overwrite
`sincpro.*` or an OpenTelemetry convention, one whose name says secret — and refused at import. At
run time an attribute that breaks those rules is dropped and logged once; without OpenTelemetry,
or outside a use case, both doors do nothing.
"""

import re
import types
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import ConfigDict

from sincpro_framework.observability.metrics.domain.paths import (
    FieldPath,
    field_path,
    refuse_foreign_paths,
    refused,
)
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

# --- the rules a key follows --------------------------------------------------------------------

KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

RESERVED_NAMESPACES = frozenset(
    {
        "sincpro",
        # OpenTelemetry semantic conventions: a key under one of these means what the
        # convention says, to every backend that reads it.
        "aws",
        "azure",
        "browser",
        "client",
        "cloud",
        "code",
        "container",
        "db",
        "deployment",
        "destination",
        "device",
        "dns",
        "enduser",
        "error",
        "exception",
        "faas",
        "gcp",
        "gen_ai",
        "host",
        "http",
        "k8s",
        "messaging",
        "net",
        "network",
        "os",
        "otel",
        "peer",
        "process",
        "rpc",
        "server",
        "service",
        "session",
        "source",
        "telemetry",
        "thread",
        "tls",
        "url",
        "user",
        "user_agent",
    }
)
"""`sincpro.*` is the framework's: `sincpro.outcome` overwritten by a use case would lie on every
span it crosses. The rest belong to OpenTelemetry's conventions."""

SENSITIVE_WORDS = frozenset(
    {
        "authorization",
        "clave",
        "contrasena",
        "cookie",
        "correo",
        "credential",
        "credentials",
        "cvc",
        "cvv",
        "email",
        "otp",
        "passwd",
        "password",
        "phone",
        "pin",
        "pwd",
        "secret",
        "tarjeta",
        "telefono",
        "token",
    }
)
SENSITIVE_PAIRS = frozenset(
    {
        ("access", "key"),
        ("api", "key"),
        ("apikey",),
        ("card", "number"),
        ("private", "key"),
        ("secret", "key"),
    }
)
"""Words that, in a key, name a secret, a credential, a card or a way to reach a person. A trace
is read by anyone with access to Grafana and kept for weeks: what opens a door or identifies a
person never goes there. The list catches the obvious name; it is not a guarantee — a personal
document number called `numero` passes, and keeping it out is the author's job."""


def key_problem(key: str) -> str | None:
    """Why `key` cannot be a span attribute of a use case, or `None` when it can."""
    if not isinstance(key, str) or not KEY.match(key):
        return (
            f"'{key}' is not a namespaced key — lowercase words joined by dots, the domain "
            "first: 'siat.nit', 'payment.merchant_id'"
        )
    words = tuple(one for part in key.split(".") for one in part.split("_"))
    namespace = key.split(".")[0]
    if namespace in RESERVED_NAMESPACES:
        owner = "the framework's" if namespace == "sincpro" else "an OpenTelemetry convention"
        return f"'{key}': '{namespace}.*' is {owner}; namespace it under the domain"
    sensitive = [one for one in words if one in SENSITIVE_WORDS]
    sensitive += [
        "_".join(pair)
        for pair in SENSITIVE_PAIRS
        if any(words[i : i + len(pair)] == pair for i in range(len(words)))
    ]
    if sensitive:
        return (
            f"'{key}' names {', '.join(sorted(sensitive))} — a secret, a credential or personal "
            "data never goes on a trace"
        )
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
    annotation = _unwrapped(annotation)
    if get_origin(annotation) is Literal:
        return all(isinstance(one, (str, bool, int, float)) for one in get_args(annotation))
    if not isinstance(annotation, type) or issubclass(annotation, bytes):
        return False
    return issubclass(annotation, (Enum, *_SCALARS))


def _members_of(annotation: Any) -> tuple[Any, ...]:
    if get_origin(annotation) in (Union, types.UnionType):
        return tuple(one for one in get_args(annotation) if one is not type(None))
    return (annotation,)


def holds_on_a_span(annotation: Any) -> bool:
    """Whether a field annotated `annotation` fits in a span attribute: a scalar, an Enum, a
    Literal, or a sequence of them — optional or a union of them too. A DTO, a mapping, bytes or
    `Any` do not: a span holds values, not documents."""
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
    span cannot hold raises `TypeError`."""
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
    path = field_path(reference, f"span attribute '{key}'")
    problem = key_problem(key)
    if problem is not None:
        raise refused(f"span attribute {path!r}: {problem}")
    if not holds_on_a_span(path.annotation):
        name = getattr(path.annotation, "__name__", repr(path.annotation))
        raise refused(
            f"span attribute {path!r}: {path.key} is {name} — a span attribute is a str, bool, "
            "int, float, Decimal, Enum, UUID, date or a sequence of them"
        )
    return SpanAttribute(key=key, path=path)


def _declaring(attributes: tuple[SpanAttribute, ...]):  # type: ignore[no-untyped-def]
    def declared[T: type](cls: T) -> T:
        refuse_foreign_paths(cls, tuple(one.path for one in attributes))
        known = {one.key for one in declared_attributes(cls)}
        for one in attributes:
            if one.key in known:
                raise refused(f"{cls.__name__}: span attribute '{one.key}' declared twice")
            known.add(one.key)
        _by_class[cls] = (*_by_class.get(cls, ()), *attributes)
        return cls

    return declared


# --- by hand, on the span of the use case running now ---------------------------------------------

_warned: set[str] = set()


def _warn_once(key: str, problem: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    try:
        logger.warning(f"traces: span attribute dropped — {problem}")
    except Exception:
        pass


def _set(span: Any, key: str, value: Any) -> None:
    try:
        converted = span_value(value)
    except TypeError as error:
        _warn_once(key, f"'{key}': {error}")
        return
    if converted is not None:
        span.set_attribute(key, converted)


def describe(span: Any, use_case: type, source: Any) -> None:
    """The attributes `use_case` declared that start at `source` — its Command before it runs,
    its Response after a success. Never raises; with no span, costs one check."""
    if span is None:
        return
    try:
        for one in declared_attributes(use_case):
            if isinstance(source, one.path.root):
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
        failed run has them) and its Response (after a success). A reference `of(X).a.b` is
        `{namespace}.a.b`; a named one, `{namespace}.{name}`."""
        if not isinstance(namespace, str) or not NAMESPACE.match(namespace):
            raise refused(
                f"span attributes: namespace '{namespace}' — lowercase words joined by dots, "
                "the domain: 'siat', 'payment'"
            )
        declared = [
            _declared(
                one, f"{namespace}.{'.'.join(field_path(one, 'a span attribute').names)}"
            )
            for one in references
        ]
        declared += [_declared(one, f"{namespace}.{name}") for name, one in named.items()]
        if not declared:
            raise refused("span attributes: nothing declared — hand at least one of(X).field")
        return _declaring(tuple(declared))

    def annotate(self, attributes: Mapping[str, SpanValue]) -> None:
        """Attributes on the span of the use case running now — for a value known only midway
        (a code the provider answered, an id computed in `execute`).

        Context: never raises. A key that breaks the rules, or a value a span cannot hold, is
        dropped and logged once; outside a use case, or with tracing off, nothing happens."""
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


__all__ = [
    "RESERVED_NAMESPACES",
    "SENSITIVE_WORDS",
    "SpanAttribute",
    "SpanValue",
    "Traces",
    "declared_attributes",
    "describe",
    "holds_on_a_span",
    "key_problem",
    "span_value",
    "traces",
]
