"""What the relay does with an event it failed to deliver — a strategy, configured.

    RetryInPlace(attempts=5, then=ParkAndContinue())      the default: blocking, in order
    RetryLater(attempts=5, then=ParkAndContinue())        non-blocking: the rest go on
    ParkAndContinue()  ·  SkipAndContinue()

**Two decisions, the way every mature consumer splits them.** First, whether to try again and
when: in place, holding everything behind the event (Spring Kafka's `DefaultErrorHandler`,
NServiceBus's immediate and delayed retries), or later, letting the rest go on (Spring Kafka's
retry topics). Second, what to do once the retries are spent: park the event where a person sees
it and go on (a dead-letter queue — Spring's `DeadLetterPublishingRecoverer`, NServiceBus's
error queue), or skip it with a log line (Kafka Connect's `errors.tolerance=all`).

**An error that cannot succeed is not retried.** `never_retry` names them — a payload that does
not validate fails the same way every time — and they go to `then` on the first failure
(Spring's not-retryable exceptions, NServiceBus's unrecoverable exceptions).

A policy decides; it never reads, publishes or writes. The relay carries the decision out.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation


class FailureOutcome(StrEnum):
    RETRY_IN_PLACE = "retry_in_place"
    """Try it again at `retry_at`; nothing after it is handed on until then."""
    RETRY_LATER = "retry_later"
    """Try it again at `retry_at`; the events after it go on — those of its entity wait behind it
    when the policy keeps a stream's order."""
    PARK = "park"
    """Keep it aside with its reason, where a person sees it and can replay it; go on."""
    SKIP = "skip"
    """Leave it, with a log line; go on."""


@dataclass(frozen=True)
class HandlingFailure:
    """An event the relay failed to deliver: which, why, how many times so far, and when."""

    event: DomainEvent
    error: Exception
    attempts: int
    """The failures so far, this one included."""
    now: datetime


@dataclass(frozen=True)
class FailureDecision:
    outcome: FailureOutcome
    retry_at: datetime | None = None
    holds_stream: bool = False
    """For `RETRY_LATER`: the later events of its entity wait behind it."""


def described(failure: HandlingFailure) -> str:
    return f"{type(failure.error).__name__}: {failure.error}"


# --- how long to wait ------------------------------------------------------------------------


class BackoffStrategy(ABC):
    """How long to wait before the next attempt."""

    @abstractmethod
    def delay(self, attempts: int) -> timedelta:
        """The wait after `attempts` failures."""


class FixedBackoff(BackoffStrategy):
    def __init__(self, delay: timedelta = timedelta(seconds=5)) -> None:
        self._delay = delay

    def delay(self, attempts: int) -> timedelta:
        return self._delay


class ExponentialBackoff(BackoffStrategy):
    """`initial`, then multiplied by `multiplier` per failure, never past `maximum` — the
    backoff Polly, Resilience4j and Spring Retry default to."""

    def __init__(
        self,
        initial: timedelta = timedelta(seconds=1),
        multiplier: float = 2.0,
        maximum: timedelta = timedelta(minutes=10),
    ) -> None:
        if multiplier < 1:
            raise ContractViolation(f"a backoff never shrinks: multiplier {multiplier} < 1")
        self.initial = initial
        self.multiplier = multiplier
        self.maximum = maximum

    def delay(self, attempts: int) -> timedelta:
        grown = self.initial * (self.multiplier ** max(attempts - 1, 0))
        return min(grown, self.maximum)


# --- what to do --------------------------------------------------------------------------------


class DeliveryFailurePolicy(ABC):
    """Decides what the relay does with an event it failed to deliver."""

    @abstractmethod
    def decide(self, failure: HandlingFailure) -> FailureDecision: ...


class ParkAndContinue(DeliveryFailurePolicy):
    """Keeps the event aside, with its reason, and goes on — a dead-letter queue."""

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        return FailureDecision(FailureOutcome.PARK)


class SkipAndContinue(DeliveryFailurePolicy):
    """Leaves the event with a log line and goes on — for a destination that can lose one."""

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        return FailureDecision(FailureOutcome.SKIP)


class _Retrying(DeliveryFailurePolicy):
    outcome: FailureOutcome

    def __init__(
        self,
        attempts: int = 5,
        backoff: BackoffStrategy | None = None,
        then: DeliveryFailurePolicy | None = None,
        never_retry: tuple[type[Exception], ...] = (),
    ) -> None:
        """`attempts` counts every try, the first included; `then` decides once they are
        spent, or at once for an error in `never_retry`."""
        if attempts < 1:
            raise ContractViolation(f"an event is tried at least once, not {attempts} times")
        self.attempts = attempts
        self.backoff = backoff or ExponentialBackoff()
        self.then = then or ParkAndContinue()
        self.never_retry = never_retry

    def _spent(self, failure: HandlingFailure) -> bool:
        return failure.attempts >= self.attempts or isinstance(
            failure.error, self.never_retry
        )

    def _again(self, failure: HandlingFailure, holds_stream: bool) -> FailureDecision:
        return FailureDecision(
            self.outcome, failure.now + self.backoff.delay(failure.attempts), holds_stream
        )


class RetryInPlace(_Retrying):
    """Tries the event again where it is, holding everything behind it, then hands it to `then`
    — the order of the events is never broken. The default."""

    outcome = FailureOutcome.RETRY_IN_PLACE

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        if self._spent(failure):
            return self.then.decide(failure)
        return self._again(failure, holds_stream=False)


class RetryLater(_Retrying):
    """Sets the event aside to try again later and goes on, then hands it to `then`. With
    `holds_stream` (the default), the later events of its entity wait behind it, so one entity's
    events still arrive in order; without it, every other event goes on."""

    outcome = FailureOutcome.RETRY_LATER

    def __init__(
        self,
        attempts: int = 5,
        backoff: BackoffStrategy | None = None,
        then: DeliveryFailurePolicy | None = None,
        never_retry: tuple[type[Exception], ...] = (),
        holds_stream: bool = True,
    ) -> None:
        super().__init__(attempts, backoff, then, never_retry)
        self.holds_stream = holds_stream

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        if self._spent(failure):
            return self.then.decide(failure)
        return self._again(failure, self.holds_stream)


DEFAULT_FAILURE_POLICY: DeliveryFailurePolicy = RetryInPlace(attempts=5)
"""Five tries in place with exponential backoff, then parked: the order holds, and a poison event
never stops the relay for good."""
