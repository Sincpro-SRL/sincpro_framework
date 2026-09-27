"""`SnippetEngine`: the one contract that runs code a definition carries.

Context: open by design — what a snippet may do is the project's decision, not the framework's.
The engine the framework ships runs Python in the same process: it checks a snippet when it is
loaded and names its lines in a traceback, but it cannot stop one that never returns — a thread
cannot be interrupted. A project that needs isolation or a hard timeout implements this for a
subprocess, CEL or WASM, and every definition keeps working.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class SnippetEngine(ABC):
    @abstractmethod
    def check(self, source: str, names: Sequence[str]) -> list[str]:
        """What is wrong with `source` before it ever runs, one sentence each: its syntax, a
        missing `return`, a name that is not one of `names`. Empty when it can run."""

    @abstractmethod
    def run(self, source: str, filename: str, values: Mapping[str, Any]) -> Any:
        """Run `source` with `values` as its names; answer what it returned. `filename` is what a
        traceback shows for its lines."""
