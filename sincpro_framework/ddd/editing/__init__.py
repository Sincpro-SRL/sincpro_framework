"""What a form does to an entity before it is saved: values assigned and derived fields
recomputed (`editing`), domain code previewed over a record nobody stores (`preview`), and
a draft activated onto its record (`drafts` — the drafts themselves the context keeps).

    assign(invoice, {"discount": "50.00", "partner_id": "p-7"})   values typed by the fields
    recompute(invoice, ["discount"])                              what depends on them, again
    with previewing() as advice: ...                              writes refused, advice kept
    refuse_stale(draft, invoice); assign(invoice, draft.values)   a draft activated
"""

from sincpro_framework.ddd.editing.drafts import (
    refuse_stale,
)
from sincpro_framework.ddd.editing.editing import assign, recompute, recompute_whole
from sincpro_framework.ddd.editing.preview import Advice, advise, is_previewing, previewing

__all__ = [
    "Advice",
    "advise",
    "assign",
    "is_previewing",
    "previewing",
    "recompute",
    "recompute_whole",
    "refuse_stale",
]
