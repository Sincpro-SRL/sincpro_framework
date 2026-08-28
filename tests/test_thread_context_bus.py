"""
Tests for `Bus.thread_context()` / `ThreadContextBus`.

`context()` overlays live in a `ContextVar`, which is isolated per OS thread by
design. A Feature/ApplicationService that fans work out to a `ThreadPoolExecutor`
(or any new thread) loses `self.context` in every worker unless the context is
explicitly propagated. These tests pin down that failure mode and verify the fix.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, cast

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context.thread_context_bus import ThreadContextBus


class ThreadDTO(DataTransferObject):
    message: str = ""
    # Only set by the concurrent-misuse test, to deterministically force two
    # threads to hold the *same* captured Context open at once, instead of
    # hoping a race materializes (which is flaky — see that test's docstring).
    entered_event: Any = None
    release_event: Any = None


class ThreadResponseDTO(DataTransferObject):
    context_data: Dict[str, Any] = {}


class ThreadAwareFeature(Feature):
    def execute(self, dto):
        if dto.entered_event is not None:
            dto.entered_event.set()
        if dto.release_event is not None:
            dto.release_event.wait(timeout=5)
        return ThreadResponseDTO(context_data=dict(self.context))


class FanOutDTO(DataTransferObject):
    workers: int = 3


class FanOutResponseDTO(DataTransferObject):
    results: list


class FanOutUsingThreadContext(ApplicationService):
    """Mirrors the real-world shape: an ApplicationService that fans work out to a
    ThreadPoolExecutor and expects every worker to see the same context.

    A fresh `thread_context()` is captured PER TASK, not once for the whole
    batch: a captured `contextvars.Context` can only be entered by one thread
    at a time, so sharing a single one across concurrent workers would raise.
    """

    def execute(self, dto: FanOutDTO):
        results = []
        with ThreadPoolExecutor(max_workers=dto.workers) as executor:
            futures = [
                executor.submit(
                    self.feature_bus.thread_context().execute,
                    ThreadDTO(message=str(i)),
                )
                for i in range(dto.workers)
            ]
            for future in as_completed(futures):
                response = cast(ThreadResponseDTO, future.result())
                results.append(response.context_data)
        return FanOutResponseDTO(results=results)


class FanOutUsingBareSubmit(ApplicationService):
    """Same shape, but submitting the raw bus — reproduces the original bug."""

    def execute(self, dto: FanOutDTO):
        results = []
        with ThreadPoolExecutor(max_workers=dto.workers) as executor:
            futures = [
                executor.submit(self.feature_bus.execute, ThreadDTO(message=str(i)))
                for i in range(dto.workers)
            ]
            for future in as_completed(futures):
                response = cast(ThreadResponseDTO, future.result())
                results.append(response.context_data)
        return FanOutResponseDTO(results=results)


def _build_framework(name: str) -> UseFramework:
    framework = UseFramework(name)

    @framework.feature(ThreadDTO)
    class _Feature(ThreadAwareFeature):
        pass

    return framework


class TestThreadContextBus:
    def test_thread_context_returns_thread_context_bus(self):
        framework = _build_framework("thread-context-type")
        framework.build_root_bus()
        assert framework.bus is not None
        bound = framework.bus.feature_bus.thread_context()
        assert isinstance(bound, ThreadContextBus)

    def test_execute_via_thread_context_preserves_context_in_new_thread(self):
        """The core fix: a worker thread submitted through `.thread_context().execute`
        sees the same context that was active when it was captured."""
        framework = _build_framework("thread-context-fix")
        framework.build_root_bus()
        assert framework.bus is not None

        with framework.context({"TOKEN": "abc123"}):
            bound = framework.bus.feature_bus.thread_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = cast(
                    ThreadResponseDTO, executor.submit(bound.execute, ThreadDTO()).result()
                )
            assert result.context_data == {"TOKEN": "abc123"}

    def test_bare_submit_loses_context_in_new_thread(self):
        """Regression guard: without thread_context(), a plain
        executor.submit(bus.execute, dto) must NOT see the context — this is the
        exact bug thread_context() exists to fix. If this assertion ever starts
        failing, ContextVar propagation semantics changed and the workaround in
        thread_context() may no longer be necessary (or may need revisiting)."""
        framework = _build_framework("thread-context-bare-bug")
        framework.build_root_bus()
        assert framework.bus is not None

        with framework.context({"TOKEN": "abc123"}):
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = cast(
                    ThreadResponseDTO,
                    executor.submit(framework.bus.feature_bus.execute, ThreadDTO()).result(),
                )
            assert result.context_data == {}

    def test_thread_context_survives_multiple_concurrent_workers(self):
        """All workers in the pool see the same captured context, not just the
        first one scheduled."""
        framework = _build_framework("thread-context-fanout")

        @framework.app_service(FanOutDTO)
        class _AppService(FanOutUsingThreadContext):
            pass

        framework.build_root_bus()

        with framework.context({"TOKEN": "xyz789", "SIAT_ENV": 1}):
            response = cast(
                FanOutResponseDTO, framework(FanOutDTO(workers=5), FanOutResponseDTO)
            )
            assert len(response.results) == 5
            for context_data in response.results:
                assert context_data == {"TOKEN": "xyz789", "SIAT_ENV": 1}

    def test_fan_out_without_thread_context_loses_context_for_every_worker(self):
        """End-to-end regression test matching the real ApplicationService shape
        (GenerateSyncDataDict) before the fix: every single worker lost context,
        not just some — which is what made the original bug so confusing to
        diagnose (it looked like a flaky/partial failure)."""
        framework = _build_framework("thread-context-fanout-bug")

        @framework.app_service(FanOutDTO)
        class _AppService(FanOutUsingBareSubmit):
            pass

        framework.build_root_bus()

        with framework.context({"TOKEN": "xyz789"}):
            response = cast(
                FanOutResponseDTO, framework(FanOutDTO(workers=5), FanOutResponseDTO)
            )
            assert all(context_data == {} for context_data in response.results)

    def test_thread_context_snapshot_is_independent_per_capture(self):
        """Capturing thread_context() at two different points in time (two
        different `with context()` blocks) must not leak one snapshot's values
        into the other."""
        framework = _build_framework("thread-context-snapshot-isolation")
        framework.build_root_bus()
        assert framework.bus is not None

        bound_first: ThreadContextBus | None = None
        bound_second: ThreadContextBus | None = None

        with framework.context({"TOKEN": "first"}):
            bound_first = framework.bus.feature_bus.thread_context()

        with framework.context({"TOKEN": "second"}):
            bound_second = framework.bus.feature_bus.thread_context()

        assert bound_first is not None
        assert bound_second is not None

        with ThreadPoolExecutor(max_workers=2) as executor:
            result_first = cast(
                ThreadResponseDTO, executor.submit(bound_first.execute, ThreadDTO()).result()
            )
            result_second = cast(
                ThreadResponseDTO, executor.submit(bound_second.execute, ThreadDTO()).result()
            )

        assert result_first.context_data == {"TOKEN": "first"}
        assert result_second.context_data == {"TOKEN": "second"}

    def test_reusing_same_thread_context_bus_concurrently_raises_clear_error(self):
        """A captured Context can't be entered by two threads at once. Reusing one
        ThreadContextBus across concurrent workers must fail loudly with guidance,
        not the raw stdlib RuntimeError — this is the exact mistake the first
        draft of FanOutUsingThreadContext made in this test file.

        The collision is forced deterministically with events (one worker holds
        the context open until told to release it) rather than firing N tasks
        and hoping two of them race — that approach is flaky: on a fast enough
        interpreter/machine every task can finish before the next one starts,
        so the "already entered" window never actually overlaps.
        """
        framework = _build_framework("thread-context-misuse")
        framework.build_root_bus()
        assert framework.bus is not None

        entered = threading.Event()
        release = threading.Event()

        with framework.context({"TOKEN": "abc123"}):
            bound = framework.bus.feature_bus.thread_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                blocking_future = executor.submit(
                    bound.execute,
                    ThreadDTO(entered_event=entered, release_event=release),
                )
                assert entered.wait(timeout=5), "worker never entered the captured context"

                try:
                    bound.execute(ThreadDTO())
                    raised = None
                except RuntimeError as error:
                    raised = error
                finally:
                    release.set()

                blocking_result = cast(ThreadResponseDTO, blocking_future.result())

            assert raised is not None, "expected a concurrent-reuse RuntimeError"
            assert "thread_context()" in str(raised)
            assert blocking_result.context_data == {"TOKEN": "abc123"}
