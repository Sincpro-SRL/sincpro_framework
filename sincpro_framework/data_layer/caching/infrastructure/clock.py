"""The clock caching judges moments by when a composition hands it none."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """The default clock of `Cache` and `QueryCaching` — aware, in UTC; a test hands its own
    (`ManualClock.now`)."""
    return datetime.now(UTC)
