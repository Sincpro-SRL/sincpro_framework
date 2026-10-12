"""What a dynamic value needs to become a literal: the instant, the timezone, and where to read."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any


@dataclass(frozen=True)
class ResolutionContext:
    """What a dynamic value needs to become a literal. Built once per use, from the server's side.

    `now` is read once and held, so every value in a filter agrees on the instant. `context` is
    the server's own (the user, the tenant); `params` is what the caller supplied for this call.
    """

    now: datetime
    timezone: tzinfo = UTC
    week_starts_on: int = 0
    """The weekday a week begins on, as `date.weekday()` counts: 0 is Monday."""

    context: Mapping[Any, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)
