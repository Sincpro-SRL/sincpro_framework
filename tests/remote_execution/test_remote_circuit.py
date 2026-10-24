"""A bus hosted by another service stops calling it while it does not answer — when asked to.

Nothing changes unless a circuit is asked for: every call is made, as before. It is asked for the
two ways an address is given — written in the address the configuration holds (`?circuit=on`),
or handed in code (`hosted_by(..., circuit=True)`, or a `Circuit` of the project's own), the code
winning. Once on, after the calls in a row that did not reach the context the calls fail at once
with the same `ContextUnavailable` — the seconds left as its `retry_after` — until one is let
through to try. An answer of the hosting service, however it failed, is an answer: it is up.
"""

import os
from collections.abc import Iterator
from datetime import timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.circuits import Circuit, CircuitOpen, CircuitState
from sincpro_framework.common.failures import retry_after
from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.common.transport.addresses import HostedAt, Wire
from sincpro_framework.common.transport.exceptions import InvalidAddress
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.exceptions import FailureKind, OutcomeUnknownError
from sincpro_framework.remote_execution import is_unreached
from sincpro_framework.remote_execution.adapters.transport import circuit_for
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.failures import error_details, raised_as_itself

from .hosting import TRANSPORTS, _free_port, host_over


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pid: int


def _caller(name: str, address: str, circuit: Circuit | bool | None = None) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            return ResponsePing(pid=os.getpid())

    bus.hosted_by(address, circuit=circuit)
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
        f"{_nobody()}&circuit=on&circuit_failures=2&circuit_open_for=60",
    )

    assert _calls(caller, 2) == [False, False]
    with pytest.raises(exceptions.ContextUnavailable) as stopped:
        caller(CommandPing(), ResponsePing)

    assert isinstance(stopped.value.__cause__, CircuitOpen)
    assert "is open" in str(stopped.value)
    assert stopped.value.retry_after is not None
    assert timedelta(seconds=59) < stopped.value.retry_after <= timedelta(seconds=60)


def test_code_asks_for_the_addresss_own_circuit_with_true():
    caller = _caller("remote-true", _nobody(), circuit=True)

    assert _calls(caller, 6) == [False] * 5 + [True]  # 5 failures in a row by default


def test_code_hands_a_circuit_of_its_own():
    own = Circuit("billing-remote", failures=1, store=InMemoryKeyValue())
    caller = _caller("remote-own", _nobody(), circuit=own)

    assert _calls(caller, 2) == [False, True]
    assert own.state() is CircuitState.OPEN


def test_code_turns_off_what_the_address_asked_for():
    caller = _caller(
        "remote-off", f"{_nobody()}&circuit=on&circuit_failures=1", circuit=False
    )

    assert _calls(caller, 4) == [False] * 4


def _from_the_host(error: Exception) -> Exception:
    """`error` as the hosting service sends it and this process raises it."""
    return raised_as_itself(
        type(error).__module__, type(error).__qualname__, str(error), error_details(error)
    )


def test_only_a_context_not_reached_counts_never_what_the_service_answered():
    circuit = circuit_for(HostedAt(Wire.GRPC, "billing:50051", circuit=True))
    assert circuit is not None
    nobody = exceptions.unreached(exceptions.ContextUnavailable("nobody answered"))
    late = exceptions.unreached(exceptions.ContextTimeout("no answer before the deadline"))
    lost = exceptions.unreached(exceptions.ContextOutcomeUnknown("lost after it was sent"))

    assert circuit.counts(nobody) and circuit.counts(late) and circuit.counts(lost)
    assert not circuit.counts(DomainError("the invoice does not balance"))
    assert not circuit.counts(exceptions.ContextFailed("KeyError", "raised there"))
    assert not circuit.counts(_from_the_host(CircuitOpen("siat", timedelta(seconds=25))))
    assert not circuit.counts(_from_the_host(OutcomeUnknownError("the signer was lost")))
    assert not circuit.counts(_from_the_host(nobody))  # its own callee did not answer it
    assert circuit_for(HostedAt(Wire.GRPC, "billing:50051")) is None


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
    own = Circuit(
        "remote-host-open", failures=1, counts=is_unreached, store=InMemoryKeyValue()
    )
    caller = (
        _caller(
            "remote-host-open",
            f"{host_with_its_own_circuit_open}?circuit=on&circuit_failures=1",
        )
        if asked == "the address"
        else _caller("remote-host-open", host_with_its_own_circuit_open, circuit=own)
    )

    for _ in range(3):  # a circuit of one failure would refuse the second call
        with pytest.raises(CircuitOpen) as answered:
            caller(CommandPing(), ResponsePing)
        assert answered.value.name == "siat"
        assert answered.value.retry_after == timedelta(seconds=25)
        assert retry_after(answered.value, FailureKind.UNAVAILABLE) == timedelta(seconds=25)

    assert own.state() is CircuitState.CLOSED


def test_the_address_circuit_waits_out_the_call_before_another_probe():
    circuit = circuit_for(HostedAt(Wire.GRPC, "billing:50051", 50, circuit=True))

    assert circuit is not None and circuit.probe_for > timedelta(seconds=50)


def test_an_address_says_its_circuit_and_writes_it_back():
    hosted = HostedAt.parse(
        "grpc://b:1?timeout=2&circuit=on&circuit_failures=3&circuit_open_for=10"
    )

    assert (hosted.circuit, hosted.circuit_failures, hosted.circuit_open_for) == (
        True,
        3,
        10.0,
    )
    assert HostedAt.parse(str(hosted)) == hosted
    assert str(HostedAt(Wire.GRPC, "b:1", 2.0)) == "grpc://b:1?timeout=2"
    assert (
        str(HostedAt(Wire.GRPC, "b:1", 2.0, circuit=True))
        == "grpc://b:1?timeout=2&circuit=on"
    )


def test_seconds_written_back_parse_back_the_same():
    hosted = HostedAt(
        Wire.GRPC, "b:1", 0.1234567, circuit=True, circuit_open_for=0.1234567891234
    )

    assert HostedAt.parse(str(hosted)) == hosted


@pytest.mark.parametrize(
    "built",
    [
        lambda: HostedAt(Wire.GRPC, "b:1", float("inf")),
        lambda: HostedAt(Wire.GRPC, "b:1", float("nan")),
        lambda: HostedAt(Wire.GRPC, "b:1", circuit=True, circuit_open_for=float("inf")),
        lambda: HostedAt(Wire.GRPC, "b:1", circuit=True, circuit_open_for=float("nan")),
        lambda: HostedAt.parse("grpc://b:1?timeout=inf"),
        lambda: HostedAt.parse("grpc://b:1?circuit=on&circuit_open_for=inf"),
        lambda: HostedAt.parse("grpc://b:1?circuit=on&circuit_failures=inf"),
    ],
)
def test_seconds_that_are_no_number_are_refused_when_the_address_is_read(built):
    with pytest.raises(InvalidAddress):
        built()


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("grpc://b:1?circuit=maybe", "circuit=maybe is neither on nor off"),
        ("grpc://b:1?circuit_failures=3", "tune a circuit that is off — add circuit=on"),
        ("grpc://b:1?circuit=on&circuit_failures=0", "circuit_failures=0 would never close"),
        ("grpc://b:1?circuit=on&circuit_failures=2.5", "circuit_failures=2.5 is not a count"),
        ("grpc://b:1?circuit=on&circuit_open_for=0", "circuit_open_for=0s is no time at all"),
    ],
)
def test_a_circuit_that_could_not_work_is_refused_saying_what_to_write(written, said):
    with pytest.raises(InvalidAddress, match=said.replace(".", r"\.")):
        HostedAt.parse(written)


def test_a_retry_after_that_travelled_as_text_is_read_as_the_time_it_says():
    unavailable = exceptions.ContextUnavailable("the host's circuit is open")
    unavailable.__dict__["retry_after"] = "PT25S"  # what a `timedelta` travels as

    assert retry_after(unavailable, FailureKind.UNAVAILABLE) == timedelta(seconds=25)
