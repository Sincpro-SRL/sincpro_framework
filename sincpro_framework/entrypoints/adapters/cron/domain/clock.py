"""The contract of what decides *when* the gateway looks at its crons."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

type OnLook = Callable[[datetime], None]


class Clock(Protocol):
    """When the gateway looks at its crons. A clock never executes anything itself: it calls
    `look(now)`, and the gateway decides what is due."""

    def now(self) -> datetime:
        """The clock's present, timezone-aware — what the gateway plans, claims and reports
        from.

        Context: the gateway never reads the system time itself, so a test that swaps the
        clock moves every decision — what is due, what was missed, `run_now`'s tick — with it.
        """
        ...

    def run(self, look: OnLook) -> None:
        """Start calling `look`. Blocks for a clock that owns a loop; returns for one driven from
        outside."""
        ...

    def stop(self) -> None:
        """Stop calling `look` — the gateway's `stop` calls it first, then lets the runs in
        progress finish."""
        ...
