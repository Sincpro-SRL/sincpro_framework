"""Async-facing pieces of the framework: anything meant for an `async def` caller.

Currently just `AsyncBus` (see `bus.py`) — the async counterpart to `Bus.thread_context()`
for callers that are themselves async and want to fan out DTOs concurrently. Future
async-only additions belong here too, so this stays the one place to look.
"""

from .bus import AsyncBus as AsyncBus

__all__ = ["AsyncBus"]
