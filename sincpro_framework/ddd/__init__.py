"""Domain vocabulary every Sincpro service shares, with no infrastructure behind it.

`ValueObject` builds a validated primitive. `Entity` is what an aggregate carries and how it
names itself for a screen. `Criteria` says what to read, `EntityCollection` what came back, `Meta`
what a model publishes about itself, `IRepository` the least a store answers, and
`DomainEvent` what happened.

Nothing here imports an ORM. Running a criteria against a database is `sincpro_framework.data_layer.orm`,
an optional extra; carrying events is `sincpro_framework.event_driven`.
"""

from sincpro_framework.ddd.criteria import (
    TEXT,
    All,
    Any,
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Cursor,
    FromContext,
    FromField,
    Grain,
    Not,
    Now,
    Offset,
    Operator,
    Pagination,
    Part,
    Period,
    Periods,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    Today,
)
from sincpro_framework.ddd.editing.editing import apply_values, recompute
from sincpro_framework.ddd.editing.edits import Edit, EditAction, EntityEdits, ResponseEdit
from sincpro_framework.ddd.editing.preview import FormWarning, read_only, warn_form
from sincpro_framework.ddd.entity import (
    ArchivableMixin,
    AuditedMixin,
    Entity,
    EventSourcedMixin,
    new_entity_id,
    utc_now,
)
from sincpro_framework.ddd.entity.checks import check
from sincpro_framework.ddd.entity.computations import compute
from sincpro_framework.ddd.entity.computed import computed
from sincpro_framework.ddd.entity.entity_collection import (
    Changes,
    Count,
    Dropped,
    EntityCollection,
)
from sincpro_framework.ddd.entity.entity_meta import (
    FieldMeta,
    FieldType,
    Meta,
    presentation_of,
)
from sincpro_framework.ddd.entity.presentation import (
    Presentation,
    When,
)
from sincpro_framework.ddd.entity.relations import (
    BusResolver,
    Relation,
    RelationAttribute,
    RelationKind,
    Resolver,
)
from sincpro_framework.ddd.events import (
    ChangeTrackingMixin,
    DeliverableEventMixin,
    DomainEvent,
    EntityUpdated,
)
from sincpro_framework.ddd.exceptions import (
    AggregateNotFound,
    ConstraintViolation,
    CurrencyMismatch,
    DomainError,
    DuplicateAggregate,
    InvalidCriteria,
    RelationNotResolved,
    StaleAggregate,
    TimedOut,
    TransactionConflict,
)
from sincpro_framework.ddd.exchange_rates import ExchangeRates, InMemoryExchangeRates
from sincpro_framework.ddd.money import Currency, ExchangeRate, Money
from sincpro_framework.ddd.multi_currency import MultiCurrency
from sincpro_framework.ddd.multi_language import MultiLanguage
from sincpro_framework.ddd.query import (
    DraftState,
    Query,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
)
from sincpro_framework.ddd.reads.defaults import NAME_SEARCH_LIMIT, detail_of, matching
from sincpro_framework.ddd.reads.reads import (
    DomainEvents,
    EntityReads,
    FieldState,
    Get,
    GetMany,
    LiteralSearch,
    PreviewEntity,
    ResponsePreviewEntity,
    Search,
    history_of,
)
from sincpro_framework.ddd.reads.violations import Violation, violations_of
from sincpro_framework.ddd.repositories import (
    AggregateRepository,
    Analyzes,
    ChangeTrackingRepositoryMixin,
    INumbering,
    IRepository,
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
    "FormWarning",
    "AggregateNotFound",
    "AggregateRepository",
    "All",
    "Analyzes",
    "Any",
    "ArchivableMixin",
    "AuditedMixin",
    "Bucket",
    "BusResolver",
    "ChangeTrackingMixin",
    "ChangeTrackingRepositoryMixin",
    "Changes",
    "Condition",
    "ConstraintViolation",
    "Count",
    "CountMode",
    "Criteria",
    "Currency",
    "CurrencyMismatch",
    "Cursor",
    "DeliverableEventMixin",
    "DomainError",
    "DomainEvent",
    "DomainEvents",
    "DraftState",
    "Dropped",
    "DuplicateAggregate",
    "Edit",
    "EditAction",
    "Entity",
    "EntityCollection",
    "EntityEdits",
    "EntityReads",
    "EntityUpdated",
    "EventSourcedMixin",
    "ExchangeRate",
    "ExchangeRates",
    "FieldMeta",
    "FieldState",
    "FieldType",
    "FromContext",
    "FromField",
    "Get",
    "GetMany",
    "Grain",
    "Hook",
    "Hooks",
    "INumbering",
    "IRepository",
    "InMemoryExchangeRates",
    "InvalidCriteria",
    "LiteralSearch",
    "Meta",
    "Money",
    "MultiCurrency",
    "MultiLanguage",
    "NAME_SEARCH_LIMIT",
    "Not",
    "Now",
    "Offset",
    "Operator",
    "Pagination",
    "Part",
    "Period",
    "Periods",
    "Pivot",
    "PivotCell",
    "Presentation",
    "PreviewEntity",
    "Query",
    "ReadsAggregates",
    "Relation",
    "RelationNotResolved",
    "RelationAttribute",
    "RelationKind",
    "Resolver",
    "ResponseEdit",
    "ResponsePaginatedQuery",
    "ResponsePreviewEntity",
    "ResponseRecord",
    "ResponseRecords",
    "Search",
    "Sort",
    "Specification",
    "StaleAggregate",
    "StoreCapabilities",
    "TEXT",
    "TimedOut",
    "Today",
    "TransactionConflict",
    "Transacts",
    "Upserted",
    "ValueObject",
    "Violation",
    "When",
    "WritesAggregates",
    "WritesInBulk",
    "warn_form",
    "apply_values",
    "check",
    "compute",
    "computed",
    "detail_of",
    "history_of",
    "matching",
    "new_entity_id",
    "presentation_of",
    "read_only",
    "violations_of",
    "recompute",
    "utc_now",
]
