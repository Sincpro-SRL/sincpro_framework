"""`Idempotency`: a write run once per key, across replicas — a retry gets the first answer.

    class CommandIssueInvoice(DataTransferObject):
        request_id: str
        total: int

        def idempotency_key(self) -> str:               # the Command says what identifies it
            return self.request_id

    idempotency = Idempotency(RedisKeyValue(client))    # or IdempotencyRecords of your database

    @billing.feature(CommandIssueInvoice)
    @idempotency.once(expires_after=timedelta(minutes=2), vary_by=("tenant_id",))
    class IssueInvoice(Feature): ...

    receipt = idempotency.run(                          # or by hand, around anything
        ("issue", request_key), issue, IdempotencyPolicy(...), JsonCodec(Receipt), payload=body
    )

Context — why claim first. *Check, run, remember* is three steps: a client whose call timed out
retries while the first is still running, on another replica, and both see "not done" and both
write. Here the first step is an atomic claim of the key, so exactly one caller runs:

1. The claim is won: the write runs, knowing its key (`current_idempotency_key()`, to hand on to
   a system that deduplicates too). Its answer is kept for `expires_after` and replayed to every
   caller with the same key; if it raises, the claim is released — a failed write stays
   retryable, only a completed answer is replayed.
2. The key is held by a completed answer for the same payload: that answer, without running.
3. The key is held for a *different* payload — a client reusing a key for another request:
   `KeyReused` (the IETF draft's 422). Nothing runs.
4. Final: the key is held by a run still in flight: waited on up to `wait_for_completion`, then
   `AlreadyInProgress` (the draft's 409) — the caller tries again later.

A claim lasts `in_progress_for`, so one left by a replica that died mid-run expires instead of
refusing the key forever — and the write may then run again: on a key-value store "once" is best
effort for a crash between the side effect and its record. `IdempotencyRecords` over the use
case's own database, completing in its transaction, closes that gap.

`once(...)` wraps the use case's own `execute`, so nothing is wired on the bus: the use case still
runs inside it — Sentry, its span, and the access guard around it, so a replayed answer is still
authorized. A subclass that keeps `execute` keeps `once()`; a `replaces=` handler with an
`execute` of its own declares its own. `AlreadyInProgress` and `KeyReused` are expected traffic:
the context lists them in its `ignore_sentry_exceptions(...)`, beside its other expected errors.
Hooks are not wrapped: a hook is a moment of a record's write, with no answer of its own.
"""

import functools
import inspect
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any, get_type_hints
from weakref import WeakKeyDictionary

from sincpro_framework.caching.adapters.json_codec import JsonCodec
from sincpro_framework.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.caching.adapters.keys import key_of
from sincpro_framework.caching.adapters.observers import SpanObserver
from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.exceptions import AlreadyInProgress, KeyReused
from sincpro_framework.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.caching.domain.idempotent_command import request_key_of
from sincpro_framework.caching.domain.observer import CacheObserver, IdempotencyOutcome
from sincpro_framework.caching.domain.policies import IdempotencyPolicy
from sincpro_framework.caching.domain.store import KeyValueStore
from sincpro_framework.caching.infrastructure.flight import POLL_SECONDS
from sincpro_framework.ddd.exceptions import ContractViolation

_running: ContextVar[str | None] = ContextVar("sincpro_idempotency_key", default=None)


def current_idempotency_key() -> str | None:
    """The key of the idempotent run in progress — `None` outside one. Hand it on (derived with
    `key_of(current_idempotency_key(), "payments")`) to another system that deduplicates by key,
    so a write that ran twice after a crash is still one write there."""
    return _running.get()


@contextmanager
def _running_as(key: str) -> Generator[None, None, None]:
    token = _running.set(key)
    try:
        yield
    finally:
        _running.reset(token)


def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _response_of(handler: type, execute: Callable[..., Any]) -> Any:
    response = get_type_hints(execute).get("return")
    if response is None:
        raise ContractViolation(
            f"{handler.__name__}.execute declares no response type: declare it, so a kept "
            "answer can be replayed as what the use case answers"
        )
    return response


class Idempotency:
    def __init__(
        self,
        store: KeyValueStore | IdempotencyRecords,
        namespace: str = "",
        observer: CacheObserver | None = None,
    ) -> None:
        """`store` is what replicas share: a `KeyValueStore` (records on it, best effort for a
        crash mid-write) or `IdempotencyRecords` of your own; `InMemoryKeyValue` covers one
        process only. `namespace` keeps these keys apart from others in the same store."""
        self.records: IdempotencyRecords = (
            store if isinstance(store, IdempotencyRecords) else KeyValueRecords(store)
        )
        self.namespace = namespace
        self.observer: CacheObserver = observer or SpanObserver()
        self._declared: WeakKeyDictionary[type, IdempotencyPolicy] = WeakKeyDictionary()
        """Every class that declared `once()`, with its policy — what `policies()` describes."""

    def _key(self, key: Any) -> str:
        prefix = f"idempotency:{self.namespace}:" if self.namespace else "idempotency:"
        return prefix + key_of(key)

    def _observe(self, outcome: IdempotencyOutcome) -> None:
        try:
            self.observer.observed(outcome, self.namespace)
        except Exception:
            return

    # Running

    def run[T](
        self,
        key: Any,
        run: Callable[[], T],
        policy: IdempotencyPolicy,
        codec: Codec[T],
        payload: Any = None,
    ) -> T:
        """`run()` once for `key` — its parameters, hashed — and its answer for every repeat.
        `payload` is what `KeyReused` compares; `None` compares nothing. See the module's context
        for the order."""
        hashed = self._key(key)
        fingerprint = "" if payload is None else key_of(payload)
        owner = uuid.uuid4().hex
        claim = IdempotencyRecord(
            state=RecordState.IN_PROGRESS, fingerprint=fingerprint, owner=owner
        )
        deadline = time.monotonic() + policy.wait_for_completion.total_seconds()
        while True:
            if self.records.claim(hashed, claim, policy.in_progress_for):
                self._observe(IdempotencyOutcome.CLAIMED)
                try:
                    with _running_as(hashed):
                        answer = run()
                except BaseException:
                    self.records.release(hashed, owner)
                    raise
                self.records.complete(
                    hashed,
                    claim.model_copy(
                        update={
                            "state": RecordState.COMPLETED,
                            "answer": codec.encode(answer),
                        }
                    ),
                    policy.expires_after,
                )
                return answer
            held = self.records.read(hashed)
            if held is None:
                continue
            if held.fingerprint != fingerprint:
                self._observe(IdempotencyOutcome.KEY_REUSED)
                raise KeyReused(
                    "this idempotency key was already used for a different payload"
                )
            if held.state == RecordState.COMPLETED:
                self._observe(IdempotencyOutcome.REPLAYED)
                return codec.decode(held.answer)
            if time.monotonic() >= deadline:
                self._observe(IdempotencyOutcome.IN_PROGRESS)
                raise AlreadyInProgress(
                    "a run with this idempotency key is still in progress"
                )
            time.sleep(POLL_SECONDS)

    # Declaring

    def once(
        self,
        expires_after: timedelta,
        in_progress_for: timedelta = timedelta(minutes=1),
        wait_for_completion: timedelta = timedelta(0),
        vary_by: tuple[str, ...] = (),
    ):
        """Run the decorated Feature or ApplicationService once per key — see
        `IdempotencyPolicy` for each knob. The key is the bus, the Command, what the Command's
        `idempotency_key()` answers (the whole Command when it has none) and the context keys in
        `vary_by`; the whole Command is the payload `KeyReused` compares."""
        policy = IdempotencyPolicy(
            expires_after=expires_after,
            in_progress_for=in_progress_for,
            wait_for_completion=wait_for_completion,
            vary_by=vary_by,
        )

        def declared[T: type](cls: T) -> T:
            """1. Refused twice on one class, and on an async `execute` — a claim held across
               an await is not supported yet.
            2. Final: `execute` replaced by one that runs the original once per key; the
               answer's codec is read from its return type at the first run.
            """
            if cls in self._declared:
                raise ContractViolation(f"{cls.__name__} declares once() twice: declare one")
            original = cls.execute  # type: ignore[attr-defined]
            if inspect.iscoroutinefunction(original):
                raise ContractViolation(
                    f"{cls.__name__}.execute is async: once() runs synchronous use cases"
                )
            codecs: list[Codec[Any]] = []

            @functools.wraps(original)
            def execute(handler: Any, dto: Any) -> Any:
                if not codecs:
                    codecs.append(JsonCodec(_response_of(cls, original)))
                context = handler.context or {}
                vary = {one: context.get(one) for one in policy.vary_by}
                return self.run(
                    (_qualified(cls), _qualified(type(dto)), request_key_of(dto), vary),
                    lambda: original(handler, dto),
                    policy,
                    codecs[0],
                    payload=(dto, vary),
                )

            cls.execute = execute  # type: ignore[attr-defined]
            self._declared[cls] = policy
            return cls

        return declared

    def policies(self) -> dict[str, IdempotencyPolicy]:
        """Each class that declared `once()`, by its full name, with its policy."""
        return {_qualified(cls): policy for cls, policy in self._declared.items()}
