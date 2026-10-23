"""A bus is never closed to what is registered on it: registered after it was built, a Feature, a
replacement, an interceptor, a dependency or an extension answers from the next execution, which
builds a new generation of the bus first.

A generation has its own buses and its own handler instances, built from every registration in
by then. The one it replaces is left as it was, so a request already running on it finishes there.
"""

import threading
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
    exceptions,
)
from sincpro_framework.auth import AccessControl, Permission
from sincpro_framework.auth.domain import exceptions as auth_exceptions
from sincpro_framework.pipelines import Pipeline
from sincpro_framework.pipelines.recorder import PipelineRecorder
from sincpro_framework.runtime.testing import granting


class CommandPing(DataTransferObject):
    pass


class CommandRelay(DataTransferObject):
    pass


class CommandLate(DataTransferObject):
    pass


class CommandCount(DataTransferObject):
    hits: int = 0


class Counted(DataTransferObject):
    hits: int


class CommandRunAll(DataTransferObject):
    run_id: str = ""


class CommandStep(DataTransferObject):
    run_id: str = ""


class ShopPermission(Permission):
    PRICE = "shop.price"


def _bus(name: str = "generations") -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> str:
            return "pong"

    @bus.app_service(CommandRelay)
    class Relay(ApplicationService):
        def execute(self, dto: CommandRelay) -> Any:
            return self.feature_bus.execute(CommandPing())

    return bus


def _late_feature(bus: UseFramework) -> None:
    @bus.feature(CommandLate)
    class Late(Feature):
        def execute(self, dto: CommandLate) -> str:
            return "late"


# --- what a generation is -----------------------------------------------------------------------


def test_a_new_generation_has_new_buses_and_new_handlers():
    bus = _bus()
    bus.build_root_bus()
    first = bus.bus
    assert first is not None

    _late_feature(bus)
    bus.build_root_bus()

    second = bus.bus
    assert second is not None and second is not first
    assert second.feature_bus is not first.feature_bus
    assert (
        second.feature_bus.feature_registry[CommandPing]
        is not first.feature_bus.feature_registry[CommandPing]
    )
    relay = second.app_service_bus.app_service_registry[CommandRelay]
    assert relay.feature_bus is second.feature_bus


def test_the_generation_replaced_keeps_answering_with_what_it_had():
    bus = _bus()
    bus.build_root_bus()
    first = bus.bus
    assert first is not None
    handler = first.feature_bus.feature_registry[CommandPing]

    _late_feature(bus)
    bus.build_root_bus()

    assert first.feature_bus.feature_registry[CommandPing] is handler
    assert CommandLate not in first.feature_bus.feature_registry
    assert first.execute(CommandRelay()) == "pong"


def test_nothing_registered_after_the_build_builds_nothing_again():
    bus = _bus()
    bus(CommandPing())
    built = bus.bus

    bus(CommandPing())
    bus.build_root_bus()

    assert bus.bus is built


def test_each_generation_wraps_a_pipeline_with_one_recorder():
    bus = UseFramework("generations-pipeline", log_after_execution=False)

    @bus.feature(CommandStep)
    class Step(Feature):
        def execute(self, dto: CommandStep) -> None:
            return None

    @bus.app_service(CommandRunAll)
    class RunAll(Pipeline, ApplicationService):
        steps = (CommandStep,)

    bus.build_root_bus()
    _late_feature(bus)
    bus.build_root_bus()

    assert bus.bus is not None
    wrapping = bus.bus.app_service_bus.interceptors[CommandRunAll]
    assert [type(one) for one in wrapping] == [PipelineRecorder]


def test_observability_starts_with_the_first_generation_only(monkeypatch):
    bus = _bus()
    started: list[object] = []
    monkeypatch.setattr(bus.observability, "start", lambda logger: started.append(logger))

    bus(CommandPing())
    _late_feature(bus)
    bus(CommandLate())

    assert len(started) == 1


# --- what answers from the next execution -------------------------------------------------------


def test_a_replacement_registered_after_the_build_answers_from_the_next_execution():
    bus = _bus()
    assert bus(CommandRelay()) == "pong"
    ping = bus.handler_of(CommandPing)
    assert ping is not None

    @bus.feature(CommandPing, replaces=ping)
    class LoudPing(Feature):
        def execute(self, dto: CommandPing) -> str:
            return "PONG"

    assert bus(CommandRelay()) == "PONG"


def test_an_extension_after_the_build_answers_whole_and_says_so_once():
    bus = _bus()
    bus(CommandPing())
    seen: list[str] = []

    def shout(dto: Any, call_next: Any) -> Any:
        seen.append("shout")
        return call_next(dto)

    def module(target: UseFramework) -> None:
        target.add_dependency("volume", 11)
        target.interceptor(CommandPing)(shout)
        _late_feature(target)

    with capture_logs() as logs:
        bus.extend(module)

    assert bus(CommandLate()) == "late"
    assert bus(CommandPing()) == "pong"
    assert seen == ["shout"]
    told = [line["event"] for line in logs if "registered after" in line["event"]]
    assert len(told) == 1 and "extension" in told[0]


def test_each_registration_after_the_build_is_said_once():
    bus = _bus()
    bus(CommandPing())

    with capture_logs() as logs:
        _late_feature(bus)
        bus.add_dependency("clock", object())

    told = [line["event"] for line in logs if "registered after" in line["event"]]
    assert len(told) == 2
    assert told[0].startswith("Feature Late") and told[1].startswith("dependency clock")


def test_an_error_handler_registered_after_the_build_answers_from_the_next_execution():
    bus = UseFramework("generations-errors", log_after_execution=False)

    @bus.feature(CommandPing)
    class Fails(Feature):
        def execute(self, dto: CommandPing) -> None:
            raise ValueError("down")

    with pytest.raises(ValueError):
        bus(CommandPing())

    bus.add_feature_error_handler(lambda error: f"handled {error}")

    assert bus(CommandPing()) == "handled down"


def test_a_late_replacement_is_guarded_by_what_it_declares():
    auth = AccessControl[ShopPermission]()
    bus = _bus("generations-guarded")
    auth.on(bus)
    assert bus(CommandPing()) == "pong"  # declares nothing: anybody runs it
    ping = bus.handler_of(CommandPing)
    assert ping is not None

    @bus.feature(CommandPing, replaces=ping)
    @auth.requires(ShopPermission.PRICE)
    class GuardedPing(Feature):
        def execute(self, dto: CommandPing) -> str:
            return "guarded"

    with pytest.raises(auth_exceptions.Unauthenticated):
        bus(CommandPing())
    with granting(ShopPermission.PRICE):
        assert bus(CommandPing()) == "guarded"


# --- when a generation cannot be built ----------------------------------------------------------


def test_a_generation_that_cannot_be_built_leaves_the_one_answering_and_recovers():
    bus = _bus()
    bus(CommandPing())
    answering = bus.bus

    @bus.interceptor(CommandLate)
    def lost(dto: Any, call_next: Any) -> Any:
        return call_next(dto)

    with pytest.raises(exceptions.UnknownDTOToExecute, match="lost.*CommandLate"):
        bus(CommandPing())
    assert bus.bus is answering

    _late_feature(bus)

    assert bus(CommandLate()) == "late"
    assert bus.bus is not answering


def test_a_bus_hosted_elsewhere_never_builds():
    bus = _bus("generations-reference")
    bus.hosted_by("http://billing.internal:8080")

    _late_feature(bus)
    bus.build_root_bus()

    assert bus.bus is None


# --- while it is running ------------------------------------------------------------------------


def test_a_request_running_keeps_its_handler_while_a_replacement_comes_in():
    bus = UseFramework("generations-in-flight", log_after_execution=False)
    entered, release = threading.Event(), threading.Event()

    @bus.feature(CommandPing)
    class Slow(Feature):
        def execute(self, dto: CommandPing) -> str:
            entered.set()
            release.wait(5)
            return "slow"

    answers: list[Any] = []
    running = threading.Thread(target=lambda: answers.append(bus(CommandPing())))
    running.start()
    assert entered.wait(5)

    @bus.feature(CommandPing, replaces=Slow)
    class Fast(Feature):
        def execute(self, dto: CommandPing) -> str:
            return "fast"

    release.set()
    running.join(5)

    assert answers == ["slow"]
    assert bus(CommandPing()) == "fast"


def test_executions_never_see_half_of_an_extension_installed_meanwhile():
    bus = UseFramework("generations-threads", log_after_execution=False)

    @bus.feature(CommandCount)
    class Count(Feature):
        def execute(self, dto: CommandCount) -> Counted:
            return Counted(hits=dto.hits)

    def hit(dto: Any, call_next: Any) -> Any:
        return call_next(dto.model_copy(update={"hits": dto.hits + 1}))

    def module(target: UseFramework) -> None:
        for _ in range(50):
            target.interceptor(CommandCount)(
                lambda dto, call_next: hit(dto, call_next)  # a distinct function each time
            )

    bus(CommandCount())
    counted: list[int] = []
    failed: list[BaseException] = []
    stop = threading.Event()

    def execute() -> None:
        while not stop.is_set():
            try:
                counted.append(bus(CommandCount(), Counted).hits)
            except BaseException as error:
                failed.append(error)

    workers = [threading.Thread(target=execute) for _ in range(8)]
    for worker in workers:
        worker.start()
    bus.extend(module)
    for _ in range(20):
        bus(CommandCount())
    stop.set()
    for worker in workers:
        worker.join(5)

    assert failed == []
    assert set(counted) <= {0, 50}
    assert bus(CommandCount(), Counted).hits == 50


# --- what stays refused -------------------------------------------------------------------------


def test_a_command_answered_by_a_feature_is_refused_as_an_application_service_where_it_is_registered():
    bus = _bus()

    with pytest.raises(exceptions.DTOAlreadyRegistered, match="CommandPing.*a feature"):

        @bus.app_service(CommandPing)
        class AlsoPing(ApplicationService):
            def execute(self, dto: CommandPing) -> None:
                return None


def test_a_command_answered_by_an_application_service_is_refused_as_a_feature_where_it_is_registered():
    bus = _bus()

    with pytest.raises(
        exceptions.DTOAlreadyRegistered, match="CommandRelay.*an application service"
    ):

        @bus.feature(CommandRelay)
        class AlsoRelay(Feature):
            def execute(self, dto: CommandRelay) -> None:
                return None
