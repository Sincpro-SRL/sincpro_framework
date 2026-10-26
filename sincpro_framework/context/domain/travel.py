"""What of a context crosses a process — the keys a header, a message or the frontend can carry.

    Travel.of(context).travels   →  the keys a header can carry
    Travel.of(context).stays     →  the keys that stay in this process, by the type they hold

**What travels is what is simple**: text, numbers, booleans, lists of them — `tenant_ids`, an
idempotency key, the identity, the keys a project adds — a mapping of those by text, one level deep
(`SINCPRO_PIPELINE_CONTEXT`), and a `Secret` holding one, as its value:
an API key the next context needs reaches it. What goes in the context is the project's decision;
the framework filters nothing. An object, a connection or a callable stays in its process — said as
data (`stays`), warned about by `sincpro_framework.context.infrastructure.travel`. How it is written
on a transport is `sincpro_framework.context.adapters.propagation`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Secret, SecretStr

from sincpro_framework.context.domain.keys import PLUMBING

_SIMPLE = (str, int, float, bool)


def _simple(value: Any) -> bool:
    """Text, a number, a boolean, or a list or tuple of only those — a mapping is `_travels`'
    to judge.

        _simple(["acme", "beta"])  ->  True
        _simple({"a": 1})          ->  False
    """
    if isinstance(value, _SIMPLE):
        return True
    return isinstance(value, (list, tuple)) and all(isinstance(one, _SIMPLE) for one in value)


def _travels(value: Any) -> bool:
    """A simple value, or a mapping of them by text — one level, as JSON carries it."""
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _simple(one) for key, one in value.items())
    return _simple(value)


@dataclass(frozen=True)
class Travel:
    """A context split by what crosses a process: what travels, and what stays here."""

    travels: dict[str, Any]
    """The keys a header can carry, in the context's order — a `Secret` as the value it holds,
    every tuple as a list."""
    stays: dict[str, str]
    """The keys that cannot travel, by the name of the type each holds — what a caller warns
    about."""

    @classmethod
    def of(cls, context: Mapping[Any, Any]) -> "Travel":
        """`context` split into what travels and what stays.

            Travel.of({"tenant_id": "acme", "tenant_ids": ("acme", "beta"),
                       "token": Secret("t0k"), "trace_id": "4bf9…", "db": connection,
                       "note": None})
                ->  Travel(travels={"tenant_id": "acme", "tenant_ids": ["acme", "beta"],
                                    "token": "t0k"},
                           stays={"db": "Connection"})

        1. A `Secret` (or `SecretStr`) is read as the value it holds.
        2. A key that is not text, the trace's plumbing (`trace_id`, `span_id`, `carrier`) or a
           `None` value is skipped, silently — neither travels nor stays.
        3. A value that travels — text, a number, a boolean, a list of them, or a one-level
           mapping of those by text — is taken, every tuple as a list (JSON's shape).
        4. Anything else stays, named by its type.
        Final: both, in the context's order.
        """
        travels: dict[str, Any] = {}
        stays: dict[str, str] = {}
        for key, value in context.items():
            if isinstance(value, (Secret, SecretStr)):
                value = value.get_secret_value()
            if not isinstance(key, str) or key in PLUMBING or value is None:
                continue
            if not _travels(value):
                stays[key] = type(value).__name__
            elif isinstance(value, Mapping):
                travels[key] = {
                    name: list(one) if isinstance(one, tuple) else one
                    for name, one in value.items()
                }
            else:
                travels[key] = list(value) if isinstance(value, tuple) else value
        return cls(travels, stays)
