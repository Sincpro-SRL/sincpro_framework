"""Domain vocabulary every Sincpro service shares, with no infrastructure behind it.

`ValueObject` builds a validated primitive. `Entity` is what an aggregate carries and how it
names itself for a screen. `Criteria` says what to read, `EntityCollection` what came back, `Meta`
what a model publishes about itself, `Repository` the least a store answers, and
`DomainEvent` what happened.

Nothing here imports an ORM. Running a criteria against a database is `sincpro_framework.orm`,
an optional extra; carrying events is `sincpro_framework.events`.
"""

from sincpro_framework.ddd.criteria import (
    All,
    Any,
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Cursor,
    Not,
    Offset,
    Operator,
    Pagination,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    holds,
    matches,
)
from sincpro_framework.ddd.entity import (
    ArchivableMixin,
    AuditedMixin,
    ChangeTrackingMixin,
    Entity,
    EntityUpdated,
    Translated,
    new_entity_id,
    utc_now,
)
from sincpro_framework.ddd.entity.entity_collection import (
    Changes,
    Count,
    Dropped,
    EntityCollection,
)
from sincpro_framework.ddd.entity.model_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.entity.relations import BusResolver, Relation, Resolver
from sincpro_framework.ddd.events import (
    DomainEvent,
    EventLogEntry,
    EventStatus,
    EventTrackableMixin,
)
from sincpro_framework.ddd.exceptions import (
    ConstraintViolation,
    ContractViolation,
    DomainError,
    DuplicateAggregate,
    InvalidCriteria,
    RelationNotResolved,
    StaleAggregate,
    TimedOut,
    TransactionConflict,
)
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.ddd.repositories import (
    AggregateRepository,
    Analyzes,
    ChangeTrackingRepositoryMixin,
    MemoryNumbering,
    MemoryRepository,
    Numbering,
    ReadsAggregates,
    Repository,
    StoreCapabilities,
    Transacts,
    Upserted,
    WritesAggregates,
    WritesInBulk,
)
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
from sincpro_framework.ddd.value_object import ValueObject

__all__ = [
    "AggregateRepository",
    "Analyzes",
    "MemoryNumbering",
    "Numbering",
    "ReadsAggregates",
    "StoreCapabilities",
    "Transacts",
    "WritesAggregates",
    "WritesInBulk",
    "All",
    "Any",
    "ArchivableMixin",
    "AuditedMixin",
    "Bucket",
    "BusResolver",
    "EntityCollection",
    "ChangeTrackingMixin",
    "Condition",
    "Changes",
    "Count",
    "CountMode",
    "Criteria",
    "Cursor",
    "DomainEvent",
    "Dropped",
    "DuplicateAggregate",
    "ConstraintViolation",
    "TransactionConflict",
    "TimedOut",
    "Entity",
    "EntityUpdated",
    "ChangeTrackingRepositoryMixin",
    "Hook",
    "Hooks",
    "EventStatus",
    "FieldMeta",
    "FieldType",
    "InvalidCriteria",
    "MemoryRepository",
    "Meta",
    "Not",
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
    "Upserted",
    "Resolver",
    "ResponsePaginatedQuery",
    "Sort",
    "Specification",
    "RelationNotResolved",
    "StaleAggregate",
    "EventTrackableMixin",
    "EventLogEntry",
    "Translated",
    "ValueObject",
    "holds",
    "matches",
    "new_entity_id",
    "utc_now",
]
