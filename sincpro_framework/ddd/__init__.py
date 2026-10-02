"""Domain vocabulary every Sincpro service shares, with no infrastructure behind it.

`ValueObject` builds a validated primitive. `Entity` is what an aggregate carries and how it
names itself for a screen. `Criteria` says what to read, `EntityCollection` what came back, `Meta`
what a model publishes about itself, `IRepository` the least a store answers, and
`DomainEvent` what happened.

Nothing here imports an ORM. Running a criteria against a database is `sincpro_framework.orm`,
an optional extra; carrying events is `sincpro_framework.event_driven`.
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
    EventSourcedMixin,
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
    DeliverableEventMixin,
    DomainEvent,
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
    INumbering,
    IRepository,
    MemoryNumbering,
    MemoryRepository,
    ReadsAggregates,
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
    "INumbering",
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
    "EventSourcedMixin",
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
    "DeliverableEventMixin",
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
    "IRepository",
    "Upserted",
    "Resolver",
    "ResponsePaginatedQuery",
    "Sort",
    "Specification",
    "RelationNotResolved",
    "StaleAggregate",
    "Translated",
    "ValueObject",
    "holds",
    "matches",
    "new_entity_id",
    "utc_now",
]
