"""Rate limiter: at most so many calls in a window of time — the others refused, or made to wait
their turn.

    from sincpro_framework.rate_limiter import RateLimiter

    purchases = RateLimiter("purchases", 100, per=timedelta(seconds=1))
    with purchases: ...          @purchases def buy(...): ...          purchases.call(buy, ...)

A limiter is part of the core and needs no library: it keeps, per key, the moment the next call is
due (GCRA) in the store where the context stores its leases — this process's memory, or the store
every replica shares. A refused
call raises `RateLimited`, `EXHAUSTED` with its `retry_after`, which every wire (429 over REST), a
pipeline's `Retry` and a queue already know what to do with.

    rate_limiter.py    RateLimiter, RateLimited — and what it counts (REFUSALS)
"""

from sincpro_framework.rate_limiter.rate_limiter import REFUSALS, RateLimited, RateLimiter

__all__ = ["REFUSALS", "RateLimited", "RateLimiter"]
