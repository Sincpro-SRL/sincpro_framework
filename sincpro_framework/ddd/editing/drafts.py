"""Activating a draft onto a record: a record changed since the draft started is refused.

    draft = use_context().drafts().read("invoice:01a1")
    refuse_stale(draft, invoice)                 StaleAggregate when the invoice moved on
    assign(invoice, draft.values)

The drafts themselves are kept by the context (`sincpro_framework.context.Drafts`): whose they are
is the context's to say; what a record does with one is the domain's.
"""

from typing import Any

from sincpro_framework.context.domain.drafts import Draft
from sincpro_framework.ddd import exceptions


def refuse_stale(draft: Draft, record: Any) -> None:
    """Refuse to activate a draft over a record that changed since the draft started.

        in      Draft(origin_version=3), Invoice(version=3)    →  nothing
        in      Draft(origin_version=3), Invoice(version=4)    →  StaleAggregate

    Compared here, before the draft's values touch the record: setting `version` back on a
    loaded record is not a check — the mapping writes it as a new value.
    """
    if record.version != draft.origin_version:
        raise exceptions.StaleAggregate(
            f"{type(record).__name__} changed since draft {draft.key} started "
            f"(version {draft.origin_version}, now {record.version}): read it again"
        )
