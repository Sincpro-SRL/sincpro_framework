"""What a form does to an entity before it is saved: values assigned and derived fields
recomputed (`editing`), domain code previewed over a record nobody stores (`preview`), and
drafts kept between requests (`drafts`)."""

from sincpro_framework.ddd.editing.drafts import (
    Draft,
    DraftConflict,
    Drafts,
    InMemoryDrafts,
    KeyValueDrafts,
    refuse_stale,
)
from sincpro_framework.ddd.editing.editing import assign, recompute, recompute_whole
from sincpro_framework.ddd.editing.preview import Advice, advise, is_previewing, previewing

__all__ = [
    "Advice",
    "Draft",
    "DraftConflict",
    "Drafts",
    "InMemoryDrafts",
    "KeyValueDrafts",
    "advise",
    "assign",
    "is_previewing",
    "previewing",
    "recompute",
    "recompute_whole",
    "refuse_stale",
]
