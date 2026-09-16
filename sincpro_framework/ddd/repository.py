"""The least a repository answers, so a use case can be written against it.

    class Repository(Protocol):
        get · search · count · save · remove

Applications use the concrete instance the adapter gives them — it has more, and that is the
point of having a concrete one. This protocol exists so an implementation is held to a minimum
surface and a test double can stand in for it; nothing in the framework types against it but
the implementations.
"""

from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity_collection import Count, EntityCollection


@runtime_checkable
class Repository(Protocol):

    def get(self, target: type, identity: Any) -> Any | None: ...

    def search(self, target: type, criteria: Criteria | None = None) -> EntityCollection: ...

    def count(self, target: type, criteria: Criteria | None = None) -> Count: ...

    def save(self, record: Any) -> None: ...

    def remove(self, record: Any) -> None: ...
