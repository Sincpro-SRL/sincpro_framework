"""What a form does to an entity before it is saved: the form's values applied and computed
fields recomputed (`editing`), and the block where nothing may be written and the warnings for the
form are collected (`preview`).

    apply_values(invoice, {"discount": "50.00", "partner_id": "p-7"})    values typed by the fields
    recompute(invoice, ["discount"])                                    what depends on them, again
    with read_only() as warnings: ...                                   writes refused, warnings held

What the framework runs by itself — every computation on save (`editing.recompute_whole`), the
check before a draft is activated (`drafts.refuse_stale`), whether a write is inside the block
(`preview.is_read_only`) — is imported from its own file.
"""

from sincpro_framework.ddd.editing.editing import apply_values, recompute
from sincpro_framework.ddd.editing.preview import FormWarning, read_only, warn_form

__all__ = [
    "FormWarning",
    "apply_values",
    "read_only",
    "recompute",
    "warn_form",
]
