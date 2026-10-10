"""Who the execution in progress acts as — kept beside the request context, never in it.

    with as_identity(Identity.user("user:42", tenant="bo", permissions={...})):
        billing(CommandIssueInvoice(...))

Context: a `ContextVar` of its own rather than a key of `bus.context(...)`. The context is a dict
anyone writes — a handler's `self.context = ...`, a header `remote_execution` adopts as it came —
so an identity kept there could be forged by writing it. Here only `as_identity` and `as_system`
set it; it follows the execution into every bus it calls and into `thread_context()`, and it does
not cross to another service: that is `AuthProvider.credentials_for`'s job.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sincpro_framework.auth.domain.identity import ANONYMOUS, Identity
from sincpro_framework.sincpro_logger import logger

_current: ContextVar[Identity] = ContextVar("sincpro_identity", default=ANONYMOUS)

_decided: ContextVar[str | None] = ContextVar("sincpro_access_decided", default=None)
"""The use case entered from outside that was let through — a nested one inherits its decision."""


def current_identity() -> Identity:
    return _current.get()


@contextmanager
def as_identity(identity: Identity) -> Generator[Identity, None, None]:
    """Act as `identity` inside the block — what an entrypoint opens once it authenticated the
    caller, and what a test opens to be someone. A use case called inside is decided anew."""
    current, decided = _current.set(identity), _decided.set(None)
    try:
        yield identity
    finally:
        _decided.reset(decided)
        _current.reset(current)


@contextmanager
def as_system(reason: str) -> Generator[Identity, None, None]:
    """Act as the system — a cron, a migration, a consumer — which every requirement admits.
    Context: `reason` is required and logged, the one trace an elevation leaves."""
    logger.info(f"acting as the system: {reason}")
    with as_identity(Identity.system(reason)) as system:
        yield system
