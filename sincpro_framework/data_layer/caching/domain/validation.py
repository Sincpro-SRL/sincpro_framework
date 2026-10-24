"""`Validation`: whether a kept value is still right — the strategy checked beside its lifetime.

    Unconditional()                                     the lifetime is the whole answer
    ExternalVersion(current=registry.version_of_tenant,  an ETag: the version the source
                    version_of=lambda tenant: tenant.version,   publishes, checked at most every
                    trusted_for=timedelta(minutes=5))     five minutes

Context: a value carries a *mark* — the version it was computed at. `holds(mark)` answers whether
that version is still current; a mark that no longer holds is never served, stale window or not.
A positive check is trusted for `trusted_for` (Caffeine's refresh-after-write): a hot key does not
put the source's version endpoint in the path of every call. The tag versions `QueryCaching`
checks are this same idea, with the repository noting the tags.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import timedelta


class Validation[T](ABC):
    """The port: the mark a value is kept with, and whether that mark still holds."""

    trusted_for: timedelta = timedelta(0)
    """How long a check that held is believed without asking again."""

    def before(self) -> str | None:
        """The mark taken before computing — `None` when it is read off the value instead."""
        return None

    def after(self, value: T, before: str | None) -> str | None:
        """The mark the computed value is kept with — `None` to answer it and keep nothing."""
        return before

    @abstractmethod
    def holds(self, mark: str) -> bool:
        """Whether a value kept with `mark` is still right."""


class Unconditional[T](Validation[T]):
    """No validator: a value is right for as long as its lifetime says."""

    def before(self) -> str | None:
        """An empty mark — taken before computing, so nothing is read off the value."""
        return ""

    def holds(self, mark: str) -> bool:
        """Always — only the freshness ends a value's life."""
        return True


class ExternalVersion[T](Validation[T]):
    """An ETag: the value is kept with the source's version, and holds while it is current."""

    def __init__(
        self,
        current: Callable[[], str],
        version_of: Callable[[T], str] | None = None,
        trusted_for: timedelta = timedelta(0),
    ) -> None:
        """`current` asks the source for its version — a cheap call, it is what every check
        costs. `version_of` reads the version off the computed value when the value carries it, which
        spares asking `current` before computing; without it, the version is asked first, so a
        change landing mid-computation is caught at the next check rather than missed."""
        self.current = current
        self.version_of = version_of
        self.trusted_for = trusted_for

    def before(self) -> str | None:
        """The source's version, asked before computing — `None` when `version_of` reads it
        off the value afterwards."""
        return None if self.version_of is not None else self.current()

    def after(self, value: T, before: str | None) -> str | None:
        """The version read off `value` by `version_of`, else the one asked before computing.

            ExternalVersion(current, version_of=lambda tenant: tenant.version)
                .after(Tenant(version="v7"), None)                            ->  "v7"

        Without `version_of`, the mark `before` took.
        """
        return self.version_of(value) if self.version_of is not None else before

    def holds(self, mark: str) -> bool:
        """Whether the source's version, asked now, is still `mark` — one call per check."""
        return self.current() == mark
