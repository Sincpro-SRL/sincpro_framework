"""How long a failed call waits before it runs again — one rule for a pipeline's step and a
queue's message: what the failure declared, as it is, and on top a wait of its own that grows each
attempt, bounded, and spread at random so callers that failed together do not come back together.
"""

from datetime import timedelta

from sincpro_framework.common.failures import backoff, declared_retry_after, retry_after
from sincpro_framework.exceptions import FailureKind

SECONDS_30 = timedelta(seconds=30)


class Throttled(Exception):
    failure_kind = FailureKind.EXHAUSTED

    def __init__(self, retry_after: object) -> None:
        super().__init__("slow down")
        self.retry_after = retry_after


def test_without_jitter_its_own_wait_grows_from_a_second_and_stops_at_its_bound():
    waits = [backoff(attempt, None, 2.0, SECONDS_30, jitter=False) for attempt in range(1, 8)]

    assert [one.total_seconds() for one in waits] == [1, 2, 4, 8, 16, 30, 30]


def test_what_the_failure_declared_is_waited_as_it_is_never_multiplied():
    declared = timedelta(seconds=18)  # a circuit open 18 more seconds

    waits = [
        backoff(attempt, declared, 2.0, timedelta(minutes=5), False) for attempt in (1, 2, 3)
    ]

    assert [one.total_seconds() for one in waits] == [19, 20, 22]


def test_callers_told_the_same_retry_after_still_come_back_apart():
    waits = [
        backoff(1, timedelta(seconds=1), 2.0, SECONDS_30, jitter=True) for _ in range(500)
    ]

    assert all(timedelta(seconds=1) <= one <= timedelta(seconds=2) for one in waits)
    assert len(set(waits)) > 100


def test_with_jitter_its_own_wait_falls_at_random_up_to_what_it_grew_to():
    waits = [backoff(3, None, 2.0, SECONDS_30, jitter=True) for _ in range(500)]

    assert all(timedelta(0) <= one <= timedelta(seconds=4) for one in waits)
    assert len(set(waits)) > 100


def test_the_retries_at_the_bound_are_spread_too():
    waits = [backoff(20, None, 2.0, SECONDS_30, jitter=True) for _ in range(200)]

    assert all(one <= SECONDS_30 for one in waits)
    assert len(set(waits)) > 100


def test_no_attempt_overflows_however_far_it_went():
    assert backoff(10_000, None, 10.0, SECONDS_30, jitter=False) == SECONDS_30


def test_a_declared_wait_past_the_bound_is_cut_to_it():
    assert backoff(1, timedelta(minutes=5), 2.0, SECONDS_30, jitter=True) == SECONDS_30


def test_what_an_error_declares_is_told_apart_from_the_default_second():
    assert declared_retry_after(Throttled(30)) == timedelta(seconds=30)
    assert declared_retry_after(Throttled("PT25S")) == timedelta(seconds=25)
    assert declared_retry_after(Throttled("soon")) is None
    assert declared_retry_after(Exception("nothing declared")) is None
    assert retry_after(Throttled(None), FailureKind.EXHAUSTED) == timedelta(seconds=1)
    assert retry_after(Throttled(0), FailureKind.EXHAUSTED) == timedelta(0)
