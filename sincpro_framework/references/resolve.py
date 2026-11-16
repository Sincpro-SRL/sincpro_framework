"""Resolving refs, as a use case every wire publishes: what a screen or an agent asks to show what
a payment, an event or a notification points at.

    class QueryResolve(Resolve):
        pass

    platform.add_dependency("refs", EntityRefs([billing, auth]))

    @platform.feature(QueryResolve)
    @rest.get("/refs")
    @mcp()
    @auth.authenticated
    class Resolution(EntityRefResolution):
        pass

    GET /platform/refs?refs=["billing.Invoice:0192…","auth.Member:0193…"]
    ->  {"records": [{"ref": "billing.Invoice:0192…", "display": "F-0042", "record": {…}},
                     {"ref": "auth.Member:0193…", "display": "Ana Pérez", "record": {…}}]}

Context: a read like any other — a `Query` — registered on the bus the project chooses, guarded
as it declares; each record behind it is read by its own `Get`, under that `Get`'s guards
(`EntityRefs`), so publishing it opens nothing a caller could not read already.
"""

from typing import Any

from pydantic import Field

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity.entity_ref import EntityRef
from sincpro_framework.ddd.query import Query
from sincpro_framework.ddd.reads.reads import dumped
from sincpro_framework.references.entity_refs import EntityRefs
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature

LIMIT = 500
"""How many refs one `Resolve` asks at most."""


class ResolvedRecord(DataTransferObject):
    """One ref a `Resolve` asked, with the record it names."""

    ref: EntityRef
    display: str = ""
    """The record as a person names it (`DEFAULT_DISPLAY`) — what a link shows; empty when its
    entity shows nothing beside its key."""
    record: dict[str, Any] | None = None
    """The record as its `Get` reads it; `None` when there is none."""


class ResponseResolve(DataTransferObject):
    """The records a `Resolve` asked for, in the order asked."""

    records: list[ResolvedRecord] = []


class Resolve(Query):
    """Records of any bounded context by their refs, each read by its own `Get`.

    QueryResolve(refs=["billing.Invoice:0192…", "auth.Member:0193…"])
    """

    refs: list[EntityRef] = Field(min_length=1, max_length=LIMIT)
    """The refs, `bounded_context.Entity:key` each — at most 500."""


class EntityRefResolution(Feature):
    """The Feature that answers `Resolve` with the dependency named `refs`: an `EntityRefs` over
    the bounded contexts it may read."""

    def resolver(self) -> EntityRefs:
        """The `EntityRefs` it reads with: the dependency named `refs`. Override it when the
        bounded context named it otherwise."""
        refs = getattr(self, "refs", None)
        if not isinstance(refs, EntityRefs):
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} resolves with the dependency named 'refs', and the bus "
                "has none: add_dependency('refs', EntityRefs([billing, …])), or override resolver()"
            )
        return refs

    def execute(self, dto: Any) -> ResponseResolve:
        """Each ref with the record it names, its display and the record as JSON — `None` where
        there is none; a refusal of any `Get` is raised."""
        records = self.resolver().resolve_many(dto.refs)
        resolved: list[ResolvedRecord] = []
        for ref, record in zip(dto.refs, records):
            if record is None:
                resolved.append(ResolvedRecord(ref=ref))
                continue
            display = type(record).DEFAULT_DISPLAY()
            shown = getattr(record, display, None) if display else None
            resolved.append(
                ResolvedRecord(
                    ref=ref,
                    display="" if shown is None else str(shown),
                    record=dumped(record),
                )
            )
        return ResponseResolve(records=resolved)
