"""`Lifetime`: the name phase 1 gave `TimeToLive` — kept so code written against it still runs.

Context: `Freshness` became a port with more than one strategy (`TimeToLive`, `Sliding`); the
time-to-live one is the same class under both names. New code names it `TimeToLive`.
"""

from sincpro_framework.caching.domain.freshness import NEVER, TimeToLive

Lifetime = TimeToLive

__all__ = ["NEVER", "Lifetime"]
