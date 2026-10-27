"""Dry run: a Command that says, in itself, whether it runs or only says what it would do.

    class CommandCreateQuotation(DryRunnable):          dry_run: bool = False, inherited
        customer: str
        total: int

    @sales.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        def preview(self, dto: CommandCreateQuotation) -> Preview: ...     validates, reads
        def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation: ...   writes

    sales(CommandCreateQuotation(customer="Juan", total=1150, dry_run=True))   ->  Preview
    sales(CommandCreateQuotation(customer="Juan", total=1150))                 ->  ResponseQuotation

The bus runs a dry run as any call — its context, its guards, its interceptors, its span — and
at the end answers it with the handler's `preview` instead of its `execute`, so `execute` never
sees one. A handler with no `preview` gets the dry run in `execute`, and decides. The field is
part of the Command, so every wire carries it as it carries any field: a CLI flag, a REST body,
an MCP tool argument.
"""

from collections.abc import Callable
from typing import Any

from pydantic import Field

from sincpro_framework.sincpro_abstractions import DataTransferObject


class DryRunnable(DataTransferObject):
    """A Command that may be run dry: `dry_run=True` answers what it would do, and does nothing.

    A project whose writes are previewed by default says so on its own base:
    `class WriteCommand(DryRunnable): dry_run: bool = True`."""

    dry_run: bool = False
    """Only say what would be done — the handler's `preview` answers, `execute` never runs."""


class Preview(DataTransferObject):
    """What a dry run answers: what would be done, as a person reads it, and whatever a screen
    shows beside it."""

    text: str
    data: dict[str, Any] = Field(default_factory=dict)
    """Structured values for a screen of the project's own; nothing reads them here."""


def answering(handler: Any, dto: Any) -> Callable[[Any], Any]:
    """What answers `dto` on `handler`: its `preview` when `dto` asks for a dry run and the
    handler has one, its `execute` otherwise.

        answering(CreateQuotation(), CommandCreateQuotation(..., dry_run=True))  ->  .preview
        answering(CreateQuotation(), CommandCreateQuotation(...))                ->  .execute
        answering(<no preview>, CommandCreateQuotation(..., dry_run=True))       ->  .execute
    """
    if isinstance(dto, DryRunnable) and dto.dry_run:
        preview = getattr(handler, "preview", None)
        if callable(preview):
            return preview
    return handler.execute
