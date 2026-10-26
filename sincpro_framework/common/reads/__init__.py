"""What a unit of reading touched: every repository read notes its aggregate, and whatever sits
above the use case — a query cache — learns what an answer depends on."""

from sincpro_framework.common.reads.noting import (
    aggregate_tag,
    note_read,
    noting_reads,
    reading,
)

__all__ = [
    "aggregate_tag",
    "note_read",
    "noting_reads",
    "reading",
]
