"""`FailurePolicy`: what a call answers when computing, or validating, a kept value fails.

    Raise()                                                 the default: the error, nothing served
    FailSafe(serve_for=timedelta(hours=1), throttle_for=timedelta(seconds=30),
             errors=[ConnectionError, TimeoutError])        the last good value, throttled

Context: a source that is down turns every call of a cached value into an error the moment the
value lapses — although the value was right a minute ago, and still is, as far as anybody knows.
`FailSafe` is RFC 5861's `stale-if-error` and FusionCache's fail-safe: past its servable life, a
kept value is still served for `serve_for` when computing it raises, and re-kept fresh for
`throttle_for`, so a source that is down is asked once per `throttle_for`, not once per call.

It never serves a value its validation *rejected*: fail-safe covers a source that cannot answer,
not one that answered no — serving it would hand out what the source revoked. A validation that
raises means "cannot tell", and that is a failure the policy handles. `BaseException`
(a cancellation, an interpreter exit) is never handled. Serving stale data is a choice, never a
surprise: `Raise` is the default, and every fallback is reported as `CacheOutcome.FALLBACK`.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import timedelta


class FailurePolicy(ABC):
    last_resort_for: timedelta = timedelta(0)
    """How long past its servable life a value may still be served when computing it fails."""
    throttle_for: timedelta = timedelta(0)
    """How long a value served as a fallback is re-kept fresh, sparing the source."""

    @abstractmethod
    def handles(self, error: Exception) -> bool:
        """Whether `error` is answered with the last good value instead of raised."""


class Raise(FailurePolicy):
    """Nothing is served past the value's servable life: the error is the answer."""

    def handles(self, error: Exception) -> bool:
        return False


class FailSafe(FailurePolicy):
    def __init__(
        self,
        serve_for: timedelta,
        throttle_for: timedelta = timedelta(seconds=30),
        errors: type[Exception] | Sequence[type[Exception]] = Exception,
    ) -> None:
        """`serve_for` is how long past its servable life the last good value may still stand
        in; `errors` the failures it stands in for — one alone or a list
        (`errors=[TimeoutError, ConnectionError]`): a timeout is not a 401."""
        self.last_resort_for = serve_for
        self.throttle_for = throttle_for
        self.errors: tuple[type[Exception], ...] = (
            (errors,) if isinstance(errors, type) else tuple(errors)
        )

    def handles(self, error: Exception) -> bool:
        return isinstance(error, self.errors)
