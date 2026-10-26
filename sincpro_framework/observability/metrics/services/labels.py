"""The labels of a measurement — which field references a declaration may label by, and the
values they read off a run (PRD_03 §4.2, §4.10).

    field_path(CommandIssueInvoice).currency
        ->  {"currency": "BOB"}  read off the Command
    field_path(BillingContext)["company"]
        ->  {"company": "3"}     read off the execution context
"""

from enum import Enum
from typing import Any, Literal, get_origin

from sincpro_framework.observability import correlation
from sincpro_framework.observability.field_paths import FieldPath, without_none
from sincpro_framework.sincpro_logger import logger

_unbounded_warned: set[str] = set()


def bounded_label(path: FieldPath) -> FieldPath:
    """A label is anything the project chooses; one that is not an `Enum`, a `Literal` or a
    `bool` is accepted with one warning.

    Context: every distinct value is one more series in the backend for as long as it lives —
    worth saying where the label is declared. What a service measures by is its decision
    (PRD_03 §4.10), so it is never refused.

        bounded_label(field_path(CommandIssueInvoice).currency)
            ->  the path  # a Currency(Enum)
        bounded_label(field_path(CommandIssueInvoice).customer_id)
            ->  the path, and the first time only the warning
                "metrics: label field_path(CommandIssueInvoice).customer_id is str, not an
                Enum, ..."

    1. The declared type of the last step, `X | None` read as `X`.
    2. A `bool`, a `Literal` or an `Enum` is bounded: the path as it is.
    3. Anything else, the first time this path (by its `repr`) is seen: one warning naming
       the path and its type, on the framework's logger — a logger that fails is ignored.
    Final: the path, always — a label is never refused.
    """
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
            logger.warning(
                f"metrics: label {where} is {name}, not an Enum, a Literal or a bool — each "
                "distinct value is one more series in the backend"
            )
        except Exception:
            pass
    return path


def label_value(value: Any) -> str:
    """How a label value travels: an Enum's value, `true`/`false`, `none`, the text.

        label_value(Currency.BOB)  ->  "BOB"        label_value(True)  ->  "true"
        label_value(None)          ->  "none"       label_value(3)     ->  "3"

    A `bool` is read before the text, so it travels as `true`, never as `True`.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


_warned: set[tuple[type, str]] = set()


def read_labels(
    paths: tuple[FieldPath, ...], sources: tuple[Any, ...], owner: type
) -> dict[str, str]:
    """Each label read off the source its path starts at — `none` when no source is one.

        read_labels((field_path(CommandIssueInvoice).currency,
                     field_path(BillingContext)["company"]),
                    (command, response), IssueInvoice)
            ->  {"currency": "BOB", "company": "3"}
        read_labels((field_path(CommandIssueInvoice).currency,), (), IssueInvoice)
            ->  {"currency": "none"}, and one warning for IssueInvoice's `currency`

    1. For each path, under its key (its last attribute):
       1.1 a context path reads the execution's context as it is now;
       1.2 a DTO path reads the first source that is an instance of its root class;
       1.3 no such source: `none`, warned about once per use case and key.
    2. Each value travels as `label_value` spells it — an Enum's value, `true`, `none`.
    Final: the labels, one per path.
    """
    labels: dict[str, str] = {}
    for path in paths:
        if path.reads_context:
            labels[path.key] = label_value(path.read(correlation.execution_context()))
            continue
        source = next((one for one in sources if isinstance(one, path.root)), None)
        if source is None:
            if (owner, path.key) not in _warned:
                _warned.add((owner, path.key))
                logger.warning(
                    f"metrics: {owner.__name__} was handed no {path.root.__name__} to read "
                    f"{path!r} from; labelled none"
                )
            labels[path.key] = "none"
            continue
        labels[path.key] = label_value(path.read(source))
    return labels
