"""`Freshness`: how long a kept value is served as is — the port, and the two the framework ships.

    TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1), early_expiry=1)
    Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))

Context: every kept value has a timeline — fresh until `fresh_until`, served stale while one
caller recomputes it until `stale_until`. A `Freshness` answers those moments and nothing else:
pure, no I/O, the same answer for the same arguments. `roll` is a uniform draw in [0, 1) the cache
supplies, so a test fixes it. A project's own proves itself with
`sincpro_framework.runtime.testing.FreshnessContract`.

`TimeToLive` — the four knobs, and the incident each prevents:

- **ttl** — the safety net. `None` keeps the value until its validation says otherwise, or
  somebody forgets it; a value kept in a shared store should always have one, or it piles up.
- **jitter** — the ttl spread by up to this fraction, up or down, so values kept together do not
  expire together and send every caller to the source in the same second.
- **stale_for** — past the ttl, the value is still served while one caller recomputes it
  (RFC 5861's stale-while-revalidate): nobody waits on a slow source for something that was
  right a moment ago.
- **early_expiry** — XFetch's β (Vattani, Chierichetti, Lowenstein, 2015): a busy value is
  recomputed before it lapses, with a probability that grows as expiry nears and with how long
  the last computation took. 0 never recomputes early.

`Sliding` is JCache's `AccessedExpiryPolicy`: a value nobody asks for goes, one asked for keeps
being served — never past `at_most` after it was computed, so a hot key is still recomputed.
"""

import math
from abc import ABC, abstractmethod
from datetime import timedelta

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject

NEVER = math.inf
"""The moment a value with no expiry stops being fresh."""


class Freshness(ABC):
    @abstractmethod
    def fresh_until(self, now: float, roll: float) -> float:
        """When a value kept at `now` stops being fresh — `NEVER` for no expiry."""

    def stale_until(self, fresh_until: float) -> float:
        """Until when a value past `fresh_until` is still served while one caller recomputes —
        never before `fresh_until`."""
        return fresh_until

    def recompute_early(
        self, fresh_until: float, took: float, now: float, roll: float
    ) -> bool:
        """Whether a fresh value is recomputed now, before it lapses."""
        return False

    def on_hit(
        self, born: float, fresh_until: float, now: float, roll: float
    ) -> float | None:
        """A new `fresh_until` for a value served at `now` — `None` leaves it as it is."""
        return None


class TimeToLive(DataTransferObject, Freshness):
    model_config = ConfigDict(frozen=True)

    ttl: timedelta | None = None
    """The safety net — `None` keeps the value until its validation says otherwise."""
    jitter: float = 0.0
    """The ttl spread by up to this fraction, up or down."""
    stale_for: timedelta = timedelta(0)
    """How long past the ttl the value is still served while one caller recomputes it."""
    early_expiry: float = 0.0
    """XFetch's β: how eagerly a value about to expire is recomputed; 0 never early."""

    def fresh_until(self, now: float, roll: float) -> float:
        if self.ttl is None:
            return NEVER
        spread = 1 + self.jitter * (2 * roll - 1)
        return now + self.ttl.total_seconds() * spread

    def stale_until(self, fresh_until: float) -> float:
        return fresh_until + self.stale_for.total_seconds()

    def recompute_early(
        self, fresh_until: float, took: float, now: float, roll: float
    ) -> bool:
        """XFetch: true with a probability that grows as `fresh_until` nears."""
        if self.early_expiry <= 0 or fresh_until == NEVER:
            return False
        return now - took * self.early_expiry * math.log(max(roll, 1e-300)) >= fresh_until


class Sliding(DataTransferObject, Freshness):
    model_config = ConfigDict(frozen=True)

    idle_for: timedelta
    """How long a value is fresh after the last time it was served."""
    at_most: timedelta
    """How long after it was computed a value is fresh at most, however often it is served."""

    def _capped(self, born: float, now: float) -> float:
        return min(now + self.idle_for.total_seconds(), born + self.at_most.total_seconds())

    def fresh_until(self, now: float, roll: float) -> float:
        return self._capped(now, now)

    def on_hit(
        self, born: float, fresh_until: float, now: float, roll: float
    ) -> float | None:
        renewed = self._capped(born, now)
        return renewed if renewed > fresh_until else None
