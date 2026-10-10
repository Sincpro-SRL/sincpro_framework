"""Every repository the domain layer answers against, with no ORM behind it.

    IRepository                     the baseline every store answers: reads and writes
    Analyzes · WritesInBulk         what a store adds when it can honour it
    Transacts · StoreCapabilities   …and what its engine honours, read at run time
    AggregateRepository[T]          one aggregate's view over a repository, for named questions
    INumbering                         gapless numbers per series and scope — a correlative
    ChangeTrackingRepositoryMixin   change tracking for a store with no flush to ask
    Hook / Hooks                    what a project puts around its own aggregates

An abstract class and not a `Protocol`: structural typing checked names only, so an
implementation with the wrong signatures passed `isinstance` and failed where it was called.

The SQLAlchemy adapter lives in `sincpro_framework.data_layer.orm`, an optional extra. It does its change
tracking on the session rather than with the mixin here, because an engine can say exactly what
it is about to write and can see writes that never went through a repository at all.
"""

from sincpro_framework.ddd.repositories.aggregate_repository import AggregateRepository
from sincpro_framework.ddd.repositories.capabilities import (
    Analyzes,
    ReadsAggregates,
    StoreCapabilities,
    Transacts,
    Upserted,
    WritesAggregates,
    WritesInBulk,
)
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
from sincpro_framework.ddd.repositories.mixins.change_tracking import (
    ChangeTrackingRepositoryMixin,
)
from sincpro_framework.ddd.repositories.numbering import INumbering
from sincpro_framework.ddd.repositories.repository import IRepository

__all__ = [
    "AggregateRepository",
    "Analyzes",
    "ChangeTrackingRepositoryMixin",
    "Hook",
    "Hooks",
    "INumbering",
    "ReadsAggregates",
    "IRepository",
    "StoreCapabilities",
    "Transacts",
    "Upserted",
    "WritesAggregates",
    "WritesInBulk",
]
