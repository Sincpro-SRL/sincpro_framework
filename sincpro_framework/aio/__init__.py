"""Async-facing pieces of the framework: anything meant for an `async def` caller.

`AsyncBus` (`bus.py`) is the async door of a bus — `billing.get_async_bus()`. `async_threads.py`
holds the async threads: where it and every other async door (REST, JSON-RPC, MCP, a queue, a
hosted context) run the buses — the process's pool, or a bus's own.
"""

from sincpro_framework.aio.bus import AsyncBus

__all__ = ["AsyncBus"]
