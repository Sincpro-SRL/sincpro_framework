"""`Freshness`: how long a cached value is served as is — the port, and the two the framework ships.

    TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1), early_expiry=1)
    Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))

Context: every cached value has a timeline — fresh until `fresh_until`, served stale while one
caller recomputes it until `stale_until`. A `Freshness` answers those moments and nothing else:
pure, no I/O, the same answer for the same arguments. `roll` is a uniform draw in [0, 1) the cache
supplies, so a test fixes it. A project's own proves itself with
`sincpro_framework.runtime.testing.FreshnessContract`.

`TimeToLive` — the four knobs, and the incident each prevents:

- **ttl** — the safety net. `None` caches the value until its validation says otherwise, or
  somebody forgets it; a value stored in a shared store should always have one, or it piles up.
- **jitter** — the ttl spread by up to this fraction, up or down, so values cached together do not
  expire together and send every caller to the source in the same second.
- **stale_for** — past the ttl, the value is still served while one caller recomputes it
  (RFC 5861's stale-while-revalidate): nobody waits on a slow source for something that was
  right a moment ago.
- **early_expiry** — XFetch's β (Vattani, Chierichetti, Lowenstein, 2015): a busy value is
  recomputed before it lapses, with a probability that grows as expiry nears and with how long
  the last computation took. 0 never recomputes early.

`Sliding` is JCache's `AccessedExpiryPolicy`: a value nobody asks for goes, one asked for is still
served — never past `at_most` after it was computed, so a hot key is still recomputed.
"""

import math
from abc import ABC, abstractmethod
from datetime import timedelta

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject

NEVER = math.inf
"""The moment a value with no expiry stops being fresh."""


class Freshness(ABC):
    """The port: the moments of one cached value's timeline, answered without I/O."""

    @abstractmethod
    def fresh_until(self, now: float, roll: float) -> float:
        """When a value cached at `now` stops being fresh — `NEVER` for no expiry."""

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
    """A fixed life from the moment a value is cached — spread, stretched stale, and recomputed
    early on demand; the four knobs are in the module header."""

    model_config = ConfigDict(frozen=True)

    ttl: timedelta | None = None
    """The safety net — `None` caches the value until its validation says otherwise."""
    jitter: float = 0.0
    """The ttl spread by up to this fraction, up or down."""
    stale_for: timedelta = timedelta(0)
    """How long past the ttl the value is still served while one caller recomputes it."""
    early_expiry: float = 0.0
    """XFetch's β: how eagerly a value about to expire is recomputed; 0 never early."""

    def fresh_until(self, now: float, roll: float) -> float:
        """`now` plus the ttl spread by `jitter` — `roll` 0 shortens it the most, 1 lengthens.

            TimeToLive(ttl=timedelta(seconds=100), jitter=0.1).fresh_until(0, 0.0)  ->  90.0
            TimeToLive(ttl=timedelta(seconds=100), jitter=0.1).fresh_until(0, 0.5)  ->  100.0
            TimeToLive().fresh_until(0, 0.5)                                         ->  NEVER

        With no ttl a value never lapses by time.
        """
        if self.ttl is None:
            return NEVER
        spread = 1 + self.jitter * (2 * roll - 1)
        return now + self.ttl.total_seconds() * spread

    def stale_until(self, fresh_until: float) -> float:
        """`stale_for` past `fresh_until` — the stale-while-revalidate window."""
        return fresh_until + self.stale_for.total_seconds()

    def recompute_early(
        self, fresh_until: float, took: float, now: float, roll: float
    ) -> bool:
        """XFetch: true with a probability that grows as `fresh_until` nears.

        Context: `now - took * β * ln(roll)` is `now` pushed forward by a random gap that
        grows with how long the last computation took (`took`) — a value slow to compute is
        recomputed sooner, so it is ready before it lapses. `roll` is clamped above zero so
        the logarithm stays finite.

        1. Never early with `early_expiry` 0, or for a value that never lapses.
        Final: whether the pushed-forward moment reaches `fresh_until`.
        """
        if self.early_expiry <= 0 or fresh_until == NEVER:
            return False
        return now - took * self.early_expiry * math.log(max(roll, 1e-300)) >= fresh_until


class Sliding(DataTransferObject, Freshness):
    """Fresh for `idle_for` after each time it is served, never past `at_most` after it was
    computed — JCache's `AccessedExpiryPolicy`."""

    model_config = ConfigDict(frozen=True)

    idle_for: timedelta
    """How long a value is fresh after the last time it was served."""
    at_most: timedelta
    """How long after it was computed a value is fresh at most, however often it is served."""

    def _capped(self, born: float, now: float) -> float:
        """`idle_for` past `now`, never later than `at_most` past `born`."""
        return min(now + self.idle_for.total_seconds(), born + self.at_most.total_seconds())

    def fresh_until(self, now: float, roll: float) -> float:
        """`idle_for` past `now` — born now, so the cap is `at_most` away; no jitter."""
        return self._capped(now, now)

    def on_hit(
        self, born: float, fresh_until: float, now: float, roll: float
    ) -> float | None:
        """Pushes `fresh_until` to `idle_for` past this hit — `None` when the cap (`at_most`
        past `born`) leaves nothing to gain.

            sliding = Sliding(idle_for=10s, at_most=60s)
            sliding.on_hit(born=0, fresh_until=15, now=8, roll=0.5)   ->  18
            sliding.on_hit(born=0, fresh_until=60, now=55, roll=0.5)  ->  None
        """
        renewed = self._capped(born, now)
        return renewed if renewed > fresh_until else None
