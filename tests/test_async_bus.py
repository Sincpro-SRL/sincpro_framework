"""Tests for `Bus.get_async_bus()` / `AsyncBus`.

`AsyncBus` is the async-caller counterpart to `ThreadContextBus`
(tests/test_thread_context_bus.py): a caller that is itself `async def` and
wants to fan out DTOs concurrently (e.g. `asyncio.gather`) still needs the
`contextvars` overlay propagated into the worker thread `asyncio.to_thread`
spawns. These tests pin down that propagation, plus the opposite reuse rule
from `ThreadContextBus`: an `AsyncBus` is stateless and safe to share across
concurrent calls.
"""

import asyncio
from typing import Any, Dict, cast

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.aio import AsyncBus

from .fixtures import CommandFeatureTest1, ResponseFeatureTest1


class AsyncThreadDTO(DataTransferObject):
    message: str = ""


class AsyncThreadResponseDTO(DataTransferObject):
    context_data: Dict[str, Any] = {}


class AsyncThreadAwareFeature(Feature):
    def execute(self, dto: AsyncThreadDTO):
        return AsyncThreadResponseDTO(context_data=dict(self.context))


class RaisingDTO(DataTransferObject):
    pass


# Deliberately NOT a RuntimeError subclass: ThreadContextBus.execute() catches
# RuntimeError to detect concurrent reuse of one captured snapshot, so a
# RuntimeError from the Feature's own business logic would be misreported as
# that error instead of propagating as-is.
class FeatureBoom(Exception):
    pass


class RaisingFeature(Feature):
    def execute(self, dto: RaisingDTO):
        raise FeatureBoom("boom")


def _build_framework(name: str) -> UseFramework:
    framework = UseFramework(name)

    @framework.feature(AsyncThreadDTO)
    class _Feature(AsyncThreadAwareFeature):
        pass

    @framework.feature(RaisingDTO)
    class _Raising(RaisingFeature):
        pass

    return framework


class TestAsyncBus:
    def test_get_async_bus_returns_async_bus(self):
        framework = _build_framework("async-bus-type")
        framework.build_root_bus()
        assert framework.bus is not None
        async_bus = framework.bus.feature_bus.get_async_bus()
        assert isinstance(async_bus, AsyncBus)

    def test_use_framework_get_async_bus_builds_lazily(self):
        framework = _build_framework("async-bus-lazy")
        assert not framework.was_initialized
        async_bus = framework.get_async_bus()
        assert framework.was_initialized
        assert isinstance(async_bus, AsyncBus)

    def test_await_execute_returns_correct_response(self, feature_bus_instance):
        async_bus = feature_bus_instance.get_async_bus()

        async def main():
            return await async_bus.execute(
                CommandFeatureTest1(to_print="Hello World"), ResponseFeatureTest1
            )

        result = asyncio.run(main())
        assert result is not None
        assert result.to_print == "Hello World"

    def test_await_call_sugar_returns_correct_response(self, feature_bus_instance):
        async_bus = feature_bus_instance.get_async_bus()

        async def main():
            return await async_bus(CommandFeatureTest1(to_print="Hello World"))

        result = asyncio.run(main())
        assert result is not None
        assert result.to_print == "Hello World"

    def test_async_bus_preserves_context_in_worker_thread(self):
        """The core fix: `await async_bus.execute(...)` sees the same context
        that was active when it was called, even though execution hops to a
        worker thread via `asyncio.to_thread`."""
        framework = _build_framework("async-bus-context")
        framework.build_root_bus()
        assert framework.bus is not None
        bus = framework.bus

        async def main():
            with framework.context({"TOKEN": "abc123"}):
                async_bus = bus.feature_bus.get_async_bus()
                return await async_bus.execute(AsyncThreadDTO())

        result = cast(AsyncThreadResponseDTO, asyncio.run(main()))
        assert result.context_data == {"TOKEN": "abc123"}

    def test_async_bus_is_reusable_across_concurrent_calls(self):
        """Unlike `ThreadContextBus`, one `AsyncBus` instance can be awaited
        concurrently many times (e.g. `asyncio.gather`) without raising —
        each call captures its own snapshot instead of reusing one up front."""
        framework = _build_framework("async-bus-concurrent-reuse")
        framework.build_root_bus()
        assert framework.bus is not None
        bus = framework.bus

        async def main():
            with framework.context({"TOKEN": "shared"}):
                async_bus = bus.feature_bus.get_async_bus()
                return await asyncio.gather(
                    *[async_bus.execute(AsyncThreadDTO(message=str(i))) for i in range(5)]
                )

        results = cast(list, asyncio.run(main()))
        assert len(results) == 5
        for raw_result in results:
            result = cast(AsyncThreadResponseDTO, raw_result)
            assert result.context_data == {"TOKEN": "shared"}

    def test_async_bus_snapshots_are_independent_per_call(self):
        """Two concurrent calls issued from two different `context()` scopes
        must not leak values into each other."""
        framework = _build_framework("async-bus-isolation")
        framework.build_root_bus()
        assert framework.bus is not None
        async_bus = framework.bus.feature_bus.get_async_bus()

        async def call_with(token: str):
            with framework.context({"TOKEN": token}):
                return await async_bus.execute(AsyncThreadDTO())

        async def main():
            return await asyncio.gather(call_with("first"), call_with("second"))

        raw_first, raw_second = asyncio.run(main())
        result_first = cast(AsyncThreadResponseDTO, raw_first)
        result_second = cast(AsyncThreadResponseDTO, raw_second)
        assert result_first.context_data == {"TOKEN": "first"}
        assert result_second.context_data == {"TOKEN": "second"}

    def test_error_from_feature_propagates_through_async_bus(self):
        framework = _build_framework("async-bus-errors")
        framework.build_root_bus()
        assert framework.bus is not None
        async_bus = framework.bus.feature_bus.get_async_bus()

        async def main():
            await async_bus.execute(RaisingDTO())

        with pytest.raises(FeatureBoom):
            asyncio.run(main())
