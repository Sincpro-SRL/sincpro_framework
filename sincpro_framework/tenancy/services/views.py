"""An isolated view of one repository — the isolation use case reached through a repository:
every call of the view runs isolated, the rest of the use case does not.

    invoices = IsolatedRepository(self.repository, lambda: isolated(discriminators))
    invoices.get(Invoice, id)            the tenant's, or None
    invoices.save(invoice)               stamped and checked
    with invoices.context() as unit:     the whole unit of work isolated
"""

from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sincpro_framework.tenancy.domain.tenant_context import TenantContext

type Isolating = Callable[[], AbstractContextManager[TenantContext]]


class IsolatedRepository:
    """A repository whose every call runs isolated — any repository: a database's, the one in
    memory, a project's own."""

    def __init__(self, repository: Any, isolating: Isolating) -> None:
        self.repository = repository
        self._isolating = isolating

    def _streamed(self, found: Iterator[Any]) -> Generator[Any]:
        """An iterator read inside the isolation, page by page, as late as it is read."""
        with self._isolating():
            yield from found

    @contextmanager
    def context(self, *args: Any, **kwargs: Any) -> Generator["IsolatedRepository"]:
        """The repository's unit of work, isolated as a whole."""
        with self._isolating(), self.repository.context(*args, **kwargs) as unit:
            yield IsolatedRepository(unit, self._isolating)

    def __getattr__(self, name: str) -> Any:
        """A method of the repository, run isolated; an iterator it answers read isolated too."""
        found = getattr(self.repository, name)
        if not callable(found):
            return found

        def isolated_call(*args: Any, **kwargs: Any) -> Any:
            with self._isolating():
                answered = found(*args, **kwargs)
            if isinstance(answered, Iterator):
                return self._streamed(answered)
            return answered

        return isolated_call
