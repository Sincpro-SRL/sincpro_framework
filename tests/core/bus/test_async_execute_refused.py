"""An `async def execute` is refused where it is registered: the bus calls `execute` and does not
await it, so it would answer an unawaited coroutine — its work never run, its errors never seen.
"""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandPing(DataTransferObject):
    pass


def test_a_feature_with_an_async_execute_is_refused_when_registered():
    bus = UseFramework("async-refused", log_after_execution=False)

    with pytest.raises(TypeError, match="async def execute"):

        @bus.feature(CommandPing)
        class Ping(Feature):
            async def execute(self, dto: CommandPing) -> None:
                return None


def test_an_application_service_with_an_async_execute_is_refused_too():
    bus = UseFramework("async-refused-app", log_after_execution=False)

    with pytest.raises(TypeError, match="get_async_bus"):

        @bus.app_service(CommandPing)
        class Ping(ApplicationService):
            async def execute(self, dto: CommandPing) -> None:
                return None
