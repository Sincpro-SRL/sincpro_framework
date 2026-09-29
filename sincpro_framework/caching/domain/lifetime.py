"""`Lifetime`: how long a kept value is served as is — the TTL strategy every kept value has.

    Lifetime(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1), early_expiry=1)

Context — the four knobs, and the incident each prevents:

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
"""

import math
from datetime import timedelta

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject

NEVER = math.inf
"""The moment a value with no ttl stops being fresh."""


class Lifetime(DataTransferObject):
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
        """When a value kept at `now` stops being fresh — `roll` is a uniform draw in [0, 1)."""
        if self.ttl is None:
            return NEVER
        spread = 1 + self.jitter * (2 * roll - 1)
        return now + self.ttl.total_seconds() * spread

    def stale_until(self, fresh_until: float) -> float:
        """Until when a value past `fresh_until` is still served while one caller recomputes."""
        return fresh_until + self.stale_for.total_seconds()

    def recompute_early(
        self, fresh_until: float, took: float, now: float, roll: float
    ) -> bool:
        """XFetch: true with a probability that grows as `fresh_until` nears."""
        if self.early_expiry <= 0 or fresh_until == NEVER:
            return False
        return now - took * self.early_expiry * math.log(max(roll, 1e-300)) >= fresh_until

    def kept_for(self, stale_until: float, now: float) -> timedelta | None:
        """What the store is told to keep the value for — its whole servable life."""
        if stale_until == NEVER:
            return None
        return timedelta(seconds=max(1.0, stale_until - now))
