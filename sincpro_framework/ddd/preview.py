"""A preview: domain code run over a record nobody will store, to answer a form's question.

    with previewing() as advice:
        assign(invoice, {"discount": "50"})
        recompute(invoice, ["discount"])
    advice                                   what the domain advised while it ran

Context: inside `previewing()` every write door of every store (`save`, `remove`, `archive`,
`upsert`, `update_all`, `remove_all`), every numbering `take`, every flush or writing statement
of a SQL session (`record_changes`, a unit of work committing what it tracks, a record added to
`repository.session` by hand, a `text(...)` statement that is not a read) and every read that
locks rows raises `WriteInPreview`, naming the call. The guard is a
context variable: a statement on a raw connection, and a thread started without the
framework's context (`ContextExecutor`), are outside it. A preview that wrote would leave a record, a number or a lock behind the
user's back, which is the one failure here worth a refusal. Outside a preview nothing changes.

`advise(...)` is how domain code speaks to a form without refusing anything: inside a preview
the advice is collected and travels back; outside one it does nothing.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Advice(DataTransferObject):
    """What the domain tells a form about the values it holds, without refusing them.

    Advice(message="a discount above 10% needs approval", field="discount")
    """

    message: str
    field: str | None = None


_ADVICE: ContextVar[list[Advice] | None] = ContextVar("sincpro_preview_advice", default=None)


@contextmanager
def previewing() -> Generator[list[Advice]]:
    """A block whose writes are refused and whose advice is collected.

    with previewing() as advice:
        repository.save(invoice)          →   WriteInPreview: save inside a preview

    A preview inside another one hands its advice to the outer one as well when it ends.
    """
    outer = _ADVICE.get()
    collected: list[Advice] = []
    token = _ADVICE.set(collected)
    try:
        yield collected
    finally:
        _ADVICE.reset(token)
        if outer is not None:
            outer.extend(collected)


def is_previewing() -> bool:
    """Whether the code running now is inside `previewing()`."""
    return _ADVICE.get() is not None


def advise(message: str, field: str | None = None) -> None:
    """Tell a form something about its values. Collected inside a preview, ignored outside.

    advise("a discount above 10% needs approval", field="discount")
    """
    collected = _ADVICE.get()
    if collected is not None:
        collected.append(Advice(message=message, field=field))
