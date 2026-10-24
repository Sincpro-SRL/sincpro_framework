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

**An error that cannot succeed is not retried.** What fails the same way every time — a payload
that does not validate, a domain refusal (`common.failures.PERMANENT`, the kinds a queue consumer
dead-letters) — goes to `then` on the first failure, and so does any error `never_retry` names
(Spring's not-retryable exceptions, NServiceBus's unrecoverable exceptions). An error that says
how long to wait (`retry_after`: a quota, a service down for now) is not tried before that.

**What a waiting retry holds.** Across passes, an event waiting for its retry keeps what it held
when it failed (`holds`): everything after it (`RetryInPlace`), the later events of its entity
(`RetryLater`, as it comes), or nothing (`RetryLater(holds_stream=False)`). A parked or skipped
event holds nothing — like a dead-letter queue, it lets its entity's later events go.

A policy decides; it never reads, publishes or writes. The relay carries the decision out.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sincpro_framework import exceptions
from sincpro_framework.common.failures import PERMANENT, refined_failure_kind, retry_after
from sincpro_framework.ddd.events import DomainEvent


class Holds(StrEnum):
    """What an event waiting for its retry holds back, pass after pass."""

    EVERYTHING = "everything"
    """Every event after it waits — the order of all of them holds."""
    ENTITY = "entity"
    """The later events of its entity wait — one entity's events arrive in order."""
    NOTHING = "nothing"
    """Every other event goes on."""


class FailureOutcome(StrEnum):
    """What the relay does with a failed event — the four answers a policy picks among."""

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
    """What a policy decided for one failure — the outcome, when to try again, and whether
    its entity's later events wait.

        FailureDecision(FailureOutcome.RETRY_LATER, retry_at=now + 30s, holds_stream=True)
        FailureDecision(FailureOutcome.PARK)
    """

    outcome: FailureOutcome
    retry_at: datetime | None = None
    holds_stream: bool = False
    """For `RETRY_LATER`: the later events of its entity wait behind it."""


# --- how long to wait ------------------------------------------------------------------------


class BackoffStrategy(ABC):
    """How long to wait before the next attempt."""

    @abstractmethod
    def delay(self, attempts: int) -> timedelta:
        """The wait after `attempts` failures."""


class FixedBackoff(BackoffStrategy):
    """The same wait after every failure — for a destination whose recovery does not depend on
    being left alone longer."""

    def __init__(self, delay: timedelta = timedelta(seconds=5)) -> None:
        """Wait `delay` every time."""
        self._delay = delay

    def delay(self, attempts: int) -> timedelta:
        """The configured wait, whatever `attempts` is."""
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
        """The curve; a `multiplier` below 1 is a `ProgrammingError` — a backoff that shrinks
        would hammer a destination that is down.

            ExponentialBackoff()                     ->  1s, 2s, 4s, … up to 10min
            ExponentialBackoff(timedelta(seconds=30), multiplier=1.0)
                ->  30s every time — a fixed wait, allowed
            ExponentialBackoff(multiplier=0.5)
                ->  raises ProgrammingError("a backoff never shrinks: multiplier 0.5 < 1")

        Refused at construction, so a misconfigured relay never starts.
        """
        if multiplier < 1:
            raise exceptions.ProgrammingError(
                f"a backoff never shrinks: multiplier {multiplier} < 1"
            )
        self.initial = initial
        self.multiplier = multiplier
        self.maximum = maximum

    def delay(self, attempts: int) -> timedelta:
        """`initial × multiplier^(attempts − 1)`, capped at `maximum`.

        ExponentialBackoff().delay(1)    ->  1s
        ExponentialBackoff().delay(4)    ->  8s
        ExponentialBackoff().delay(20)   ->  10min   (the maximum)
        """
        grown = self.initial * (self.multiplier ** max(attempts - 1, 0))
        return min(grown, self.maximum)


# --- what to do --------------------------------------------------------------------------------


class DeliveryFailurePolicy(ABC):
    """Decides what the relay does with an event it failed to deliver."""

    holds: Holds = Holds.NOTHING
    """What an event this policy sent back to wait for its retry holds, in the passes after."""

    @abstractmethod
    def decide(self, failure: HandlingFailure) -> FailureDecision:
        """What to do with `failure` — a pure decision: no reading, publishing or writing."""
        ...


class ParkAndContinue(DeliveryFailurePolicy):
    """Keeps the event aside, with its reason, and goes on — a dead-letter queue."""

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        """Park, whatever the failure."""
        return FailureDecision(FailureOutcome.PARK)


class SkipAndContinue(DeliveryFailurePolicy):
    """Leaves the event with a log line and goes on — for a destination that can lose one."""

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        """Skip, whatever the failure."""
        return FailureDecision(FailureOutcome.SKIP)


class _Retrying(DeliveryFailurePolicy):
    """What both retrying policies share: the attempts, the backoff, the fallback and the
    errors never retried — they differ only in the outcome and what a wait holds."""

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
            raise exceptions.ProgrammingError(
                f"an event is tried at least once, not {attempts} times"
            )
        self.attempts = attempts
        self.backoff = backoff or ExponentialBackoff()
        self.then = then or ParkAndContinue()
        self.never_retry = never_retry

    def decided(self, failure: HandlingFailure, holds_stream: bool) -> FailureDecision:
        """The decision of a retrying policy, with `outcome` and `holds_stream` its own.

            RetryLater(attempts=3), failure at attempt 1  ->  RETRY_LATER at now + 1s
            RetryLater(attempts=3), failure at attempt 3  ->  PARK (then decides)

        1. Spent — the last attempt, an error `never_retry` names, or one of a kind that
           fails the same way every time (`PERMANENT`): `then` decides.
        Final: tried again after the backoff, or after what the error asks to wait
           (`retry_after`), whichever is longer."""
        kind = refined_failure_kind(failure.error)
        if (
            failure.attempts >= self.attempts
            or isinstance(failure.error, self.never_retry)
            or kind in PERMANENT
        ):
            return self.then.decide(failure)
        wait = max(
            self.backoff.delay(failure.attempts),
            retry_after(failure.error, kind) or timedelta(0),
        )
        return FailureDecision(self.outcome, failure.now + wait, holds_stream)


class RetryInPlace(_Retrying):
    """Tries the event again where it is, holding everything behind it — in this pass and in the
    ones after, until it went out or `then` decided — so no event overtakes it. The default.
    """

    outcome = FailureOutcome.RETRY_IN_PLACE
    holds = Holds.EVERYTHING

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        """Retry in place — the hold is `Holds.EVERYTHING`, so no stream flag is needed."""
        return self.decided(failure, holds_stream=False)


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
        """As `RetryInPlace`, plus `holds_stream`: whether its entity's later events wait."""
        super().__init__(attempts, backoff, then, never_retry)
        self.holds_stream = holds_stream
        self.holds = Holds.ENTITY if holds_stream else Holds.NOTHING

    def decide(self, failure: HandlingFailure) -> FailureDecision:
        """Retry later, holding its entity's stream when configured to."""
        return self.decided(failure, self.holds_stream)


DEFAULT_FAILURE_POLICY: DeliveryFailurePolicy = RetryInPlace(attempts=5)
"""Five tries in place with exponential backoff, then parked: the order holds, and a poison event
never stops the relay for good."""
