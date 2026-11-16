"""References to records of any bounded context: `EntityRef` names one as a text
(`billing.Invoice:0192…`), `EntityRefs` reads it by its own `Get`, and `Resolve` is that read as a
use case every wire publishes.

    refs = EntityRefs([billing, auth])
    refs.resolve(EntityRef.parse("billing.Invoice:0192…"))      the record, under its guards
"""

from sincpro_framework.ddd.entity.entity_ref import EntityRef
from sincpro_framework.references.entity_refs import EntityRefs
from sincpro_framework.references.resolve import (
    EntityRefResolution,
    Resolve,
    ResolvedRecord,
    ResponseResolve,
)

__all__ = [
    "EntityRef",
    "EntityRefResolution",
    "EntityRefs",
    "ResolvedRecord",
    "Resolve",
    "ResponseResolve",
]
