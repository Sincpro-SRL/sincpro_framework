"""An isolated view of one repository: every call of the view runs isolated, the rest of the use
case does not.

    invoices = IsolatedRepository(self.repository, isolating)
    invoices.get(Invoice, id)                 the tenant's, or None
    invoices.narrowed(c).fetch_all(Invoice)   still isolated
    with invoices.context() as unit: ...      the whole unit of work isolated
"""

from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sincpro_framework.ddd.repositories import AggregateRepository, IRepository
from sincpro_framework.tenancy.domain.tenant_context import TenantContext

type Isolating = Callable[[], AbstractContextManager[TenantContext]]


class IsolatedRepository:
    """Any repository — a database's, the one in memory, an aggregate's view, a project's own —
    whose every call runs isolated."""

    def __init__(self, repository: Any, isolating: Isolating) -> None:
        self.repository = repository
        self._isolating = isolating

    def _streamed(self, found: Iterator[Any]) -> Generator[Any]:
        """Context: a stream is read page by page after the call returned — each page inside the
        isolation, as late as it is read."""
        with self._isolating():
            yield from found

    def _kept_isolated(self, answered: Any) -> Any:
        """Context: what a call answers may be a repository again (`narrowed`, a unit of work)
        or a stream — each stays isolated."""
        if isinstance(answered, (IRepository, AggregateRepository)):
            return IsolatedRepository(answered, self._isolating)
        if isinstance(answered, Iterator):
            return self._streamed(answered)
        return answered

    @contextmanager
    def context(self, *args: Any, **kwargs: Any) -> Generator["IsolatedRepository"]:
        """The repository's unit of work, isolated as a whole."""
        with self._isolating(), self.repository.context(*args, **kwargs) as unit:
            yield IsolatedRepository(unit, self._isolating)

    def __getattr__(self, name: str) -> Any:
        found = getattr(self.repository, name)
        if not callable(found):
            return found

        def isolated_call(*args: Any, **kwargs: Any) -> Any:
            with self._isolating():
                return self._kept_isolated(found(*args, **kwargs))

        return isolated_call
