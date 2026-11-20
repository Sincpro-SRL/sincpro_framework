"""How long a failed call waits before it runs again — one rule for a pipeline's step and a
queue's message: what the failure declared, grown each attempt, bounded, and spread at random so
callers that failed together do not come back together."""

from datetime import timedelta

from sincpro_framework.common.failures import backoff, declared_retry_after, retry_after
from sincpro_framework.exceptions import FailureKind

SECONDS_30 = timedelta(seconds=30)


class Throttled(Exception):
    failure_kind = FailureKind.EXHAUSTED

    def __init__(self, retry_after: object) -> None:
        super().__init__("slow down")
        self.retry_after = retry_after


def test_without_jitter_the_wait_grows_from_a_second_and_stops_at_its_bound():
    waits = [backoff(attempt, None, 2.0, SECONDS_30, jitter=False) for attempt in range(1, 8)]

    assert [one.total_seconds() for one in waits] == [1, 2, 4, 8, 16, 30, 30]


def test_what_the_failure_declared_is_where_the_wait_starts():
    assert backoff(2, timedelta(seconds=5), 2.0, SECONDS_30, jitter=False) == timedelta(
        seconds=10
    )


def test_with_jitter_the_wait_falls_at_random_up_to_what_it_grew_to():
    waits = [backoff(3, None, 2.0, SECONDS_30, jitter=True) for _ in range(500)]

    assert all(timedelta(0) <= one <= timedelta(seconds=4) for one in waits)
    assert len(set(waits)) > 100


def test_with_jitter_a_wait_never_falls_under_what_the_failure_declared():
    waits = [
        backoff(2, timedelta(seconds=5), 2.0, SECONDS_30, jitter=True) for _ in range(500)
    ]

    assert all(timedelta(seconds=5) <= one <= timedelta(seconds=10) for one in waits)


def test_a_declared_wait_past_the_bound_is_cut_to_it():
    assert backoff(1, timedelta(minutes=5), 2.0, SECONDS_30, jitter=True) == SECONDS_30


def test_what_an_error_declares_is_told_apart_from_the_default_second():
    assert declared_retry_after(Throttled(30)) == timedelta(seconds=30)
    assert declared_retry_after(Throttled("PT25S")) == timedelta(seconds=25)
    assert declared_retry_after(Throttled("soon")) is None
    assert declared_retry_after(Exception("nothing declared")) is None
    assert retry_after(Throttled(None), FailureKind.EXHAUSTED) == timedelta(seconds=1)
    assert retry_after(Throttled(0), FailureKind.EXHAUSTED) == timedelta(0)
