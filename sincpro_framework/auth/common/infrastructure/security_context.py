"""Who the execution in progress acts as — held beside the request context, never in it.

    with as_identity(Identity.user("user:42", tenant="bo", permissions={...})):
        billing(CommandIssueInvoice(...))

Context: a `ContextVar` of its own rather than a key of `bus.context(...)`. The context is a dict
anyone writes — a handler's `self.context = ...`, a header `remote_execution` adopts as it came —
so an identity stored there could be forged by writing it. Here only `as_identity` and `as_system`
set it; it follows the execution into every bus it calls and into `thread_context()`, and it does
not cross to another service: that is `AuthProvider.credentials_for`'s job.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sincpro_framework.auth.common.domain.identity import ANONYMOUS, Identity
from sincpro_framework.context import use_context
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.sincpro_logger import logger

_current: ContextVar[Identity] = ContextVar("sincpro_identity", default=ANONYMOUS)

_access_control: ContextVar[Any] = ContextVar("sincpro_access_control", default=None)
"""The `AccessControl` guarding the use case in progress — what an outgoing call asks first for
the credentials it carries, before any other of the process."""


def acting_tenant(identity: Identity, context: Any = None) -> str | None:
    """The tenant `identity` acts in: its own — the tenant its credential was issued for —, else
    the one the call names (`tenant_id` of the execution context). One rule for every decision
    auth makes per tenant: the requirements added at runtime, the roles assigned, the
    entitlements, the credentials of an outgoing call.

        acting_tenant(Identity.user("ana", tenant="bo"))           ->  "bo"
        acting_tenant(Identity.user("ana"))   # call names "pe"   ->  "pe"

    Context: an identity of no tenant — a person of several — acts where the call says; whether
    the caller may name that tenant is tenancy's to validate (`Tenancy(memberships=...)`), and
    an identity of a tenant naming another is refused by the guard.
    """
    if identity.tenant is not None:
        return identity.tenant
    values = context if context is not None else use_context()
    return values.get(ContextKey.TENANT_ID) or None


def current_access_control() -> Any:
    """The `AccessControl` guarding the use case in progress — `None` outside a guarded one."""
    return _access_control.get()


@contextmanager
def as_access_control(access: Any) -> Generator[None, None, None]:
    """`access` guards the execution inside the block — what the guard opens around the use case
    it let run, restored on leaving."""
    token = _access_control.set(access)
    try:
        yield
    finally:
        _access_control.reset(token)


_presented: ContextVar[str | None] = ContextVar("sincpro_presented_credential", default=None)
"""The bearer token the caller presented, while the call it authenticated runs — what a token
exchange acts for. Never on the `Identity`: it is serialized, logged and carried to other
services, and a token must be none of those."""


def current_presented_token() -> str | None:
    """The bearer token the caller of the execution in progress presented — `None` outside an
    authenticated call, or when it presented none."""
    return _presented.get()


@contextmanager
def as_presented_token(token: str | None) -> Generator[None, None, None]:
    """`token` is the presented credential inside the block — set by the entrypoint that
    authenticated it, and restored on leaving."""
    previous = _presented.set(token)
    try:
        yield
    finally:
        _presented.reset(previous)


def current_identity() -> Identity:
    """Who the execution in progress acts as — `Identity.anonymous()` outside any
    `as_identity`, never `None`."""
    return _current.get()


@contextmanager
def as_identity(identity: Identity) -> Generator[Identity, None, None]:
    """Act as `identity` inside the block — what an entrypoint opens once it authenticated the
    caller, and what a test opens to be someone. A use case called inside is decided anew.

        with as_identity(Identity.user("user:42", tenant="bo")) as identity:
            current_identity().subject   ->  "user:42"
        current_identity().subject       ->  "anonymous"    # as it was before the block

    Context: the identity is restored on leaving, even when the block raises.
    """
    current = _current.set(identity)
    try:
        yield identity
    finally:
        _current.reset(current)


@contextmanager
def as_system(reason: str) -> Generator[Identity, None, None]:
    """Act as the system — a cron, a migration, a consumer — which every requirement admits.
    Context: `reason` is required and logged, the one trace an elevation leaves."""
    logger.info(f"acting as the system: {reason}")
    with as_identity(Identity.system(reason)) as system:
        yield system
