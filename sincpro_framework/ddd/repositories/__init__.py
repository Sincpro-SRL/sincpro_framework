"""Every repository the domain layer answers against, with no ORM behind it.

    Repository                      the abstract store: what a use case is written against
    MemoryRepository                one that answers it over records held in memory
    ChangeTrackingRepositoryMixin   change tracking for a store with no flush to ask
    Hook / Hooks / Rule             what a project puts around its own aggregates

An abstract class and not a `Protocol`: structural typing checked names only, so an
implementation with the wrong signatures passed `isinstance` and failed where it was called.

The SQLAlchemy adapter lives in `sincpro_framework.orm`, an optional extra. It does its change
tracking on the session rather than with the mixin here, because an engine can say exactly what
it is about to write and can see writes that never went through a repository at all.
"""

from sincpro_framework.ddd.repositories.change_tracking import ChangeTrackingRepositoryMixin
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks, Rule
from sincpro_framework.ddd.repositories.memory_repository import MemoryRepository
from sincpro_framework.ddd.repositories.repository import Repository

__all__ = [
    "ChangeTrackingRepositoryMixin",
    "Hook",
    "Hooks",
    "MemoryRepository",
    "Repository",
    "Rule",
]
