"""A Feature / ApplicationService is built once per UseFramework and serves every execution.

That is the contract consumers build on, and the one a class-based handler most easily
breaks: anything written to ``self`` during ``execute`` is shared with every other
execution, including concurrent ones on other threads. Request data belongs in local
variables. These tests pin the lifetime so a change to it is a decision, not an accident.
"""

from concurrent.futures import ThreadPoolExecutor

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandRemember(DataTransferObject):
    value: int


class ResponseRemember(DataTransferObject):
    value: int
    handler_id: int
    previous: int | None


class CommandOrchestrate(DataTransferObject):
    value: int


def _framework() -> UseFramework:
    framework = UseFramework("handler-lifetime", log_after_execution=False)

    @framework.feature(CommandRemember)
    class Remember(Feature):
        def execute(self, dto: CommandRemember) -> ResponseRemember:
            previous = getattr(self, "last_value", None)
            self.last_value = dto.value
            return ResponseRemember(value=dto.value, handler_id=id(self), previous=previous)

    @framework.app_service(CommandOrchestrate)
    class Orchestrate(ApplicationService):
        def execute(self, dto: CommandOrchestrate) -> ResponseRemember:
            response = self.feature_bus.execute(
                CommandRemember(value=dto.value), ResponseRemember
            )
            assert response is not None
            return ResponseRemember(
                value=dto.value, handler_id=id(self), previous=response.previous
            )

    return framework


def test_feature_instance_serves_every_execution():
    framework = _framework()

    first = framework(CommandRemember(value=1), ResponseRemember)
    second = framework(CommandRemember(value=2), ResponseRemember)

    assert first is not None and second is not None
    assert first.handler_id == second.handler_id
    assert second.previous == 1, "state on self leaks into the next execution"


def test_application_service_instance_serves_every_execution():
    framework = _framework()

    first = framework(CommandOrchestrate(value=1), ResponseRemember)
    second = framework(CommandOrchestrate(value=2), ResponseRemember)

    assert first is not None and second is not None
    assert first.handler_id == second.handler_id


def test_concurrent_executions_share_one_instance():
    framework = _framework()

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda value: framework(CommandRemember(value=value), ResponseRemember),
                range(40),
            )
        )

    assert all(response is not None for response in responses)
    assert len({response.handler_id for response in responses if response}) == 1
    assert [response.value for response in responses if response] == list(range(40))
