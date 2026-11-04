"""A preview: domain code run over a record nobody will store, to answer a form's question.

    with read_only() as warnings:
        apply_values(invoice, {"discount": "50"})
        recompute(invoice, ["discount"])
    warnings                                   what the domain warned the form about

Context: inside `read_only()` every write door of every store (`save`, `remove`, `archive`,
`upsert`, `update_all`, `remove_all`), every numbering `take`, every flush or writing statement
of a SQL session (`record_changes`, a unit of work committing what it tracks, a record added to
`repository.session` by hand, a `text(...)` statement that is not a read) and every read that
locks rows raises `ProgrammingError`, naming the call. The guard is a context variable: a
statement on a raw connection, and a thread started without the framework's context
(`ContextExecutor`), are outside it. A preview that wrote would leave a record, a number or a
lock behind the user's back, which is the one failure here worth a refusal. Outside the block
nothing changes.

`warn_form(...)` is how domain code warns a form without refusing anything: inside the block the
warning is collected and travels back; outside it, nothing happens. A warning given by a record
an aggregate owns — a line of an invoice, from its own computation — says where it is (`path`),
as an error does, so a screen puts it on that line.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sincpro_framework.sincpro_abstractions import DataTransferObject


class FormWarning(DataTransferObject):
    """A warning to a form about the values it holds; it refuses nothing.

    FormWarning(message="a discount above 10% needs approval", field="discount", path=("discount",))
    FormWarning(message="price below cost", field="lines", path=("lines", 2, "price"))

    `path` is where it points inside the record — a line's cell included, by its position in the
    list the form sent — and `field` the record's own field it is under (`path[0]`); both empty
    when the warning is about the record as a whole.
    """

    message: str
    field: str | None = None
    path: tuple[str | int, ...] = ()


_WARNINGS: ContextVar[list[FormWarning] | None] = ContextVar(
    "sincpro_form_warnings", default=None
)

_AT: ContextVar[tuple[str | int, ...]] = ContextVar("sincpro_form_warnings_at", default=())


@contextmanager
def read_only() -> Generator[list[FormWarning]]:
    """A block where every write is refused and the warnings for the form are collected.

    with read_only() as warnings:
        repository.save(invoice)          →   ProgrammingError: save inside a preview

    A block inside another one hands its warnings to the outer one as well when it ends.
    """
    outer = _WARNINGS.get()
    collected: list[FormWarning] = []
    token = _WARNINGS.set(collected)
    try:
        yield collected
    finally:
        _WARNINGS.reset(token)
        if outer is not None:
            outer.extend(collected)


@contextmanager
def warnings_at(path: tuple[str | int, ...]) -> Generator[None]:
    """A block whose warnings point at `path` inside the record: what `recompute` opens around
    each record an aggregate owns, so a line's warning lands on that line.

    with warnings_at(("lines", 2)):
        warn_form("price below cost", field="price")    ->  path ("lines", 2, "price")

    Blocks inside one another add up: a line of a line is under both.
    """
    token = _AT.set((*_AT.get(), *path))
    try:
        yield
    finally:
        _AT.reset(token)


def is_read_only() -> bool:
    """Whether the code running now is inside `read_only()`."""
    return _WARNINGS.get() is not None


def located(path: tuple[str | int, ...]) -> tuple[str | int, ...]:
    """`path`, given by the record whose code runs now, as seen from the aggregate: where
    `warnings_at` placed that record, in front of it.

        # in InvoiceLine's own code, computing the third line
        located(("price",))    ->  ("lines", 2, "price")
    """
    return (*_AT.get(), *path)


def warn_form_at(message: str, path: tuple[str | int, ...]) -> None:
    """A warning to the form pointing at `path` inside the aggregate, already located — what a
    rule that points into one of its lines gives. Collected inside `read_only()`, ignored
    outside it.

        warn_form_at("name repeated", ("repositories", 3, "name"))
            ->  FormWarning(field="repositories", path=("repositories", 3, "name"))
    """
    collected = _WARNINGS.get()
    if collected is None:
        return
    collected.append(
        FormWarning(message=message, field=str(path[0]) if path else None, path=path)
    )


def warn_form(message: str, field: str | None = None) -> None:
    """Warn the form about its values, refusing nothing. Collected inside `read_only()`, ignored
    outside it.

    warn_form("a discount above 10% needs approval", field="discount")
        ->  FormWarning(field="discount", path=("discount",))
    # in InvoiceLine's own computation, computing the third line
    warn_form("price below cost", field="price")
        ->  FormWarning(field="lines", path=("lines", 2, "price"))

    `field` is the field of the record whose code is running; where that record sits inside the
    aggregate is added in front of it.
    """
    warn_form_at(message, located(() if field is None else (field,)))
