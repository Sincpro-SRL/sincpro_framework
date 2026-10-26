"""A bus hosted by another service stops calling it while it does not answer — when asked to.

Nothing changes unless a circuit is asked for: every call is made, as before. It is asked for the
two ways an address is given — written in the address the configuration holds (`?circuit_breaker=on`),
or handed in code (`hosted_by(..., circuit_breaker=True)`, or a `CircuitBreaker` of the project's own), the code
winning. Once on, after the calls in a row that did not reach the context the calls fail at once
with the same `ContextUnavailable` — the seconds left as its `retry_after` — until one is let
through to try. An answer of the hosting service, however it failed, is an answer: it is up.
"""

import os
from collections.abc import Iterator
from datetime import timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.circuit_breaker import CircuitBreaker, CircuitOpen, CircuitState
from sincpro_framework.common.failures import retry_after
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.exceptions import FailureKind, OutcomeUnknownError
from sincpro_framework.remote_execution import HostedAt, InvalidAddress, Scheme
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.failure_codec import FailureCodec
from sincpro_framework.remote_execution.services.transports import circuit_breaker_for

from .hosting import TRANSPORTS, _free_port, host_over


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pid: int


def _caller(
    name: str, address: str, circuit_breaker: CircuitBreaker | bool | None = None
) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            return ResponsePing(pid=os.getpid())

    bus.hosted_by(address, circuit_breaker=circuit_breaker)
    return bus


def _nobody() -> str:
    return f"http://127.0.0.1:{_free_port()}?timeout=2"


def _calls(caller: UseFramework, times: int) -> list[bool]:
    """For each call, whether it was refused by an open circuit — not made."""
    refused: list[bool] = []
    for _ in range(times):
        with pytest.raises(exceptions.ContextUnavailable) as unavailable:
            caller(CommandPing(), ResponsePing)
        refused.append(isinstance(unavailable.value.__cause__, CircuitOpen))
    return refused


def test_with_no_circuit_asked_for_every_call_is_made():
    assert _calls(_caller("remote-plain", _nobody()), 7) == [False] * 7


def test_the_address_asks_for_one_and_an_open_one_says_what_is_left():
    caller = _caller(
        "remote-configured",
        f"{_nobody()}&circuit_breaker=on&circuit_breaker_failures=2&circuit_breaker_open_for=60",
    )

    assert _calls(caller, 2) == [False, False]
    with pytest.raises(exceptions.ContextUnavailable) as stopped:
        caller(CommandPing(), ResponsePing)

    assert isinstance(stopped.value.__cause__, CircuitOpen)
    assert "is open" in str(stopped.value)
    assert stopped.value.retry_after is not None
    assert timedelta(seconds=59) < stopped.value.retry_after <= timedelta(seconds=60)


def test_code_asks_for_the_addresss_own_circuit_with_true():
    caller = _caller("remote-true", _nobody(), circuit_breaker=True)

    assert _calls(caller, 6) == [False] * 5 + [True]  # 5 failures in a row by default


def test_code_hands_a_circuit_of_its_own():
    own = CircuitBreaker("billing-remote", failures=1, store=InMemoryKeyValue())
    caller = _caller("remote-own", _nobody(), circuit_breaker=own)

    assert _calls(caller, 2) == [False, True]
    assert own.state() is CircuitState.OPEN


def test_code_turns_off_what_the_address_asked_for():
    caller = _caller(
        "remote-off",
        f"{_nobody()}&circuit_breaker=on&circuit_breaker_failures=1",
        circuit_breaker=False,
    )

    assert _calls(caller, 4) == [False] * 4


def _from_the_host(error: Exception) -> Exception:
    """`error` as the hosting service sends it and this process raises it."""
    return FailureCodec.rebuilt(
        type(error).__module__,
        type(error).__qualname__,
        str(error),
        FailureCodec.details(error),
    )


def test_only_a_context_not_reached_counts_never_what_the_service_answered():
    circuit = circuit_breaker_for(
        HostedAt(Scheme.GRPC, "billing:50051", circuit_breaker=True)
    )
    assert circuit is not None
    nobody = exceptions.ContextUnavailable("nobody answered", unreached=True)
    late = exceptions.ContextTimeout("no answer before the deadline", unreached=True)
    lost = exceptions.ContextOutcomeUnknown("lost after it was sent", unreached=True)

    assert circuit.counts(nobody) and circuit.counts(late) and circuit.counts(lost)
    assert not circuit.counts(DomainError("the invoice does not balance"))
    assert not circuit.counts(exceptions.ContextFailed("KeyError", "raised there"))
    assert not circuit.counts(_from_the_host(CircuitOpen("siat", timedelta(seconds=25))))
    assert not circuit.counts(_from_the_host(OutcomeUnknownError("the signer was lost")))
    assert not circuit.counts(_from_the_host(nobody))  # its own callee did not answer it
    assert circuit_breaker_for(HostedAt(Scheme.GRPC, "billing:50051")) is None


@pytest.fixture(params=TRANSPORTS)
def host_with_its_own_circuit_open(request: pytest.FixtureRequest) -> Iterator[str]:
    """A service hosting `CommandPing` whose own dependency is down: its circuit is open."""
    host = UseFramework("remote-host-open", log_after_execution=False)

    @host.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            raise CircuitOpen("siat", timedelta(seconds=25))

    yield from host_over(request.param, [host])


@pytest.mark.parametrize("asked", ["the address", "a project's circuit"])
def test_a_hosts_own_outage_reaches_the_caller_as_itself_and_never_opens_its_circuit(
    host_with_its_own_circuit_open, asked
):
    """The host's own circuit in front of SIAT is open: its callers are told so — with its
    `retry_after` — and their circuit, which only counts the host not reached, stays closed.
    """
    own = CircuitBreaker(
        "remote-host-open",
        failures=1,
        counts=lambda error: getattr(error, "unreached", False),
        store=InMemoryKeyValue(),
    )
    caller = (
        _caller(
            "remote-host-open",
            f"{host_with_its_own_circuit_open}?circuit_breaker=on&circuit_breaker_failures=1",
        )
        if asked == "the address"
        else _caller("remote-host-open", host_with_its_own_circuit_open, circuit_breaker=own)
    )

    for _ in range(3):  # a circuit of one failure would refuse the second call
        with pytest.raises(CircuitOpen) as answered:
            caller(CommandPing(), ResponsePing)
        assert answered.value.name == "siat"
        assert answered.value.retry_after == timedelta(seconds=25)
        assert retry_after(answered.value, FailureKind.UNAVAILABLE) == timedelta(seconds=25)

    assert own.state() is CircuitState.CLOSED


def test_the_address_circuit_waits_out_the_call_before_another_probe():
    circuit = circuit_breaker_for(
        HostedAt(Scheme.GRPC, "billing:50051", 50, circuit_breaker=True)
    )

    assert circuit is not None and circuit.probe_for > timedelta(seconds=50)


def test_an_address_says_its_circuit_and_writes_it_back():
    hosted = HostedAt.parse(
        "grpc://b:1?timeout=2&circuit_breaker=on&circuit_breaker_failures=3&circuit_breaker_open_for=10"
    )

    assert (
        hosted.circuit_breaker,
        hosted.circuit_breaker_failures,
        hosted.circuit_breaker_open_for,
    ) == (
        True,
        3,
        10.0,
    )
    assert HostedAt.parse(str(hosted)) == hosted
    assert str(HostedAt(Scheme.GRPC, "b:1", 2.0)) == "grpc://b:1?timeout=2"
    assert (
        str(HostedAt(Scheme.GRPC, "b:1", 2.0, circuit_breaker=True))
        == "grpc://b:1?timeout=2&circuit_breaker=on"
    )


def test_seconds_written_back_parse_back_the_same():
    hosted = HostedAt(
        Scheme.GRPC,
        "b:1",
        0.1234567,
        circuit_breaker=True,
        circuit_breaker_open_for=0.1234567891234,
    )

    assert HostedAt.parse(str(hosted)) == hosted


@pytest.mark.parametrize(
    "built",
    [
        lambda: HostedAt(Scheme.GRPC, "b:1", float("inf")),
        lambda: HostedAt(Scheme.GRPC, "b:1", float("nan")),
        lambda: HostedAt(
            Scheme.GRPC, "b:1", circuit_breaker=True, circuit_breaker_open_for=float("inf")
        ),
        lambda: HostedAt(
            Scheme.GRPC, "b:1", circuit_breaker=True, circuit_breaker_open_for=float("nan")
        ),
        lambda: HostedAt.parse("grpc://b:1?timeout=inf"),
        lambda: HostedAt.parse("grpc://b:1?circuit_breaker=on&circuit_breaker_open_for=inf"),
        lambda: HostedAt.parse("grpc://b:1?circuit_breaker=on&circuit_breaker_failures=inf"),
    ],
)
def test_seconds_that_are_no_number_are_refused_when_the_address_is_read(built):
    with pytest.raises(InvalidAddress):
        built()


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("grpc://b:1?circuit_breaker=maybe", "circuit_breaker=maybe is neither on nor off"),
        (
            "grpc://b:1?circuit_breaker_failures=3",
            "tune a circuit breaker that is off — add circuit_breaker=on",
        ),
        (
            "grpc://b:1?circuit_breaker=on&circuit_breaker_failures=0",
            "circuit_breaker_failures=0 would never close",
        ),
        (
            "grpc://b:1?circuit_breaker=on&circuit_breaker_failures=2.5",
            "circuit_breaker_failures=2.5 is not a count",
        ),
        (
            "grpc://b:1?circuit_breaker=on&circuit_breaker_open_for=0",
            "circuit_breaker_open_for=0s is no time at all",
        ),
    ],
)
def test_a_circuit_that_could_not_work_is_refused_saying_what_to_write(written, said):
    with pytest.raises(InvalidAddress, match=said.replace(".", r"\.")):
        HostedAt.parse(written)


def test_a_retry_after_that_travelled_as_text_is_read_as_the_time_it_says():
    unavailable = exceptions.ContextUnavailable("the host's circuit is open")
    unavailable.__dict__["retry_after"] = "PT25S"  # what a `timedelta` travels as

    assert retry_after(unavailable, FailureKind.UNAVAILABLE) == timedelta(seconds=25)
