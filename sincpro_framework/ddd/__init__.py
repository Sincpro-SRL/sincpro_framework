"""Domain vocabulary every Sincpro service shares, with no infrastructure behind it.

`ValueObject` builds a validated primitive. `Entity` is what an aggregate carries and how it
names itself for a screen. `Criteria` says what to read, `EntityCollection` what came back, `Meta`
what a model publishes about itself, `Repository` the least a store answers, and
`DomainEvent` what happened.

Nothing here imports an ORM. Running a criteria against a database is `sincpro_framework.orm`,
an optional extra; carrying events is `sincpro_framework.events`.
"""

from sincpro_framework.ddd.criteria import (
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pivot,
    PivotCell,
    Sort,
    Specification,
)
from sincpro_framework.ddd.entity import (
    Archivable,
    Audited,
    Entity,
    Translated,
    new_entity_id,
    utc_now,
)
from sincpro_framework.ddd.entity_collection import Changes, Count, Dropped, EntityCollection
from sincpro_framework.ddd.evaluate import matches
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DomainError,
    DuplicateAggregate,
    InvalidCriteria,
    RelationNotResolved,
    StaleAggregate,
)
from sincpro_framework.ddd.memory_repository import MemoryRepository
from sincpro_framework.ddd.model_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.pagination import Cursor, Offset, Pagination
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.ddd.relations import BusResolver, Relation, Resolver
from sincpro_framework.ddd.repository import Repository
from sincpro_framework.ddd.value_object import ValueObject

__all__ = [
    "Archivable",
    "Audited",
    "Bucket",
    "BusResolver",
    "EntityCollection",
    "Condition",
    "Changes",
    "Count",
    "CountMode",
    "Criteria",
    "Cursor",
    "DomainEvent",
    "Dropped",
    "DuplicateAggregate",
    "Entity",
    "FieldMeta",
    "FieldType",
    "InvalidCriteria",
    "MemoryRepository",
    "Meta",
    "Offset",
    "Operator",
    "DomainError",
    "ContractViolation",
    "Pagination",
    "Pivot",
    "PivotCell",
    "Query",
    "Relation",
    "Repository",
    "Resolver",
    "ResponsePaginatedQuery",
    "Sort",
    "Specification",
    "RelationNotResolved",
    "StaleAggregate",
    "Translated",
    "ValueObject",
    "matches",
    "new_entity_id",
    "utc_now",
]
