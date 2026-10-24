"""`Idempotency`: a write run once per key, across replicas — a retry gets the first answer.

    class CommandIssueInvoice(DataTransferObject):
        request_id: str
        total: int

        def idempotency_key(self) -> str:               # the Command says what identifies it
            return self.request_id

    @billing.feature(CommandIssueInvoice)
    @billing.idempotency                                # where the context is kept: nothing built
    class IssueInvoice(Feature): ...

    @billing.idempotency.exactly_once(records)          # the answer in the write's transaction

    idempotency = Idempotency(RedisKeyValue(client))    # or built by hand, on a store of its own
    @idempotency.once(expires_after=timedelta(minutes=2), vary_by="tenant_id")

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
case's own database, completing in its transaction (`records.transaction()`), closes that gap
— `DatabaseIdempotencyRecords`. A release that fails is said, never raised over the write's
error: the claim expires all the same.

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
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any, cast, get_type_hints
from weakref import WeakKeyDictionary

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.entrypoint.owner import BY_TENANT_AND_USER, owner_of
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.data_layer.caching.adapters.json_codec import JsonCodec
from sincpro_framework.data_layer.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.data_layer.caching.adapters.keys import key_of
from sincpro_framework.data_layer.caching.adapters.observers import (
    IDEMPOTENCY_OUTCOMES,
    traced_and_measured,
)
from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.codec import Codec
from sincpro_framework.data_layer.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.data_layer.caching.domain.idempotent_command import (
    IDEMPOTENCY_KEY,
    request_key_of,
)
from sincpro_framework.data_layer.caching.domain.observer import (
    CacheObserver,
    IdempotencyOutcome,
)
from sincpro_framework.data_layer.caching.domain.policies import IdempotencyPolicy
from sincpro_framework.data_layer.caching.infrastructure.flight import POLL_SECONDS
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.store import KeyValueStore
from sincpro_framework.store.entrypoint.resolution import current_store, key_value_store_of

_running: ContextVar[str | None] = ContextVar("sincpro_idempotency_key", default=None)


def current_idempotency_key() -> str | None:
    """The key of the idempotent run in progress — `None` outside one. Hand it on (derived with
    `key_of(current_idempotency_key(), "payments")`) to another system that deduplicates by key,
    so a write that ran twice after a crash is still one write there."""
    return _running.get()


@contextmanager
def _running_as(key: str) -> Generator[None, None, None]:
    """`key` is the idempotent run in progress for the block — what
    `current_idempotency_key()` answers inside the write; the previous one comes back when it
    ends."""
    token = _running.set(key)
    try:
        yield
    finally:
        _running.reset(token)


def _released(records: IdempotencyRecords, key: str, owner: str) -> None:
    """The claim let go of after a failed run — or, when the store fails too, said: the claim
    expires after `in_progress_for`, and the run's own error is the one raised."""
    try:
        records.release(key, owner)
    except Exception as error:
        logger.warning(
            f"the claim on idempotency key {key} was not released ({error}): it expires after "
            "in_progress_for, and the key is refused until then"
        )


_once_wrappers: WeakKeyDictionary[Callable[..., Any], IdempotencyPolicy] = WeakKeyDictionary()
"""Every `execute` that `once()` wrote, with its policy — what `declares_once` looks up."""


def declares_once(cls: type) -> bool:
    """Whether `cls` runs once per key: its `execute`, as Python resolves it, is one `once()`
    wrote. A subclass that keeps `execute` answers `True`; a replacement with an `execute` of its
    own answers `False` until it declares `once()` itself.

    Context: an entrypoint that exposes use cases (a retry-safe flag, an `Idempotency-Key`
    header) asks this instead of reading how the method happens to be named."""
    execute = getattr(cls, "execute", None)
    return execute is not None and execute in _once_wrappers


def _qualified(cls: type) -> str:
    """`module.Qualname` — how `policies()` names a class: `billing.features.IssueInvoice`."""
    return f"{cls.__module__}.{cls.__qualname__}"


def _response_of(handler: type, execute: Callable[..., Any]) -> Any:
    """The response type `execute` declares — what a kept answer is decoded back into for a
    replay; one that declares none is a mistake: `ProgrammingError`, saying to declare it."""
    response = get_type_hints(execute).get("return")
    if response is None:
        raise exceptions.ProgrammingError(
            f"{handler.__name__}.execute declares no response type: declare it, so a kept "
            "answer can be replayed as what the use case answers"
        )
    return response


_context_records: WeakKeyDictionary[KeyValueStore, KeyValueRecords] = WeakKeyDictionary()
"""The records on each store the context stands on — built once per store."""


def _records_here() -> IdempotencyRecords:
    """The records on the store of the running context: the bus in play, else the process,
    else this process's memory."""
    store = key_value_store_of(current_store())
    records = _context_records.get(store)
    if records is None:
        records = _context_records[store] = KeyValueRecords(store)
    return records


class Idempotency:
    """Writes run once per key on shared records — declared on a use case (`once(...)`) or
    around any callable (`run(...)`). The facades (`billing.idempotency`) are built on it."""

    def __init__(
        self,
        store: KeyValueStore | IdempotencyRecords | None = None,
        namespace: str = "",
        observer: CacheObserver | None = None,
        records_of: Callable[[], IdempotencyRecords] | None = None,
    ) -> None:
        """`store` is what replicas share: a `KeyValueStore` (records on it, best effort for a
        crash mid-write) or `IdempotencyRecords` of your own (transactional, exactly once).
        With none, the records are where the context is kept — `records_of` says which, asked
        at every run (a bus's facade passes its own); without it, the running context's.
        `namespace` keeps these keys apart from others in the same store.

            Idempotency(RedisKeyValue(redis), namespace="billing")
                ->  `KeyValueRecords` on that store, keys "idempotency:billing:<hash>"
            Idempotency(DatabaseIdempotencyRecords(...))   ->  those records, exactly once
            Idempotency()   ->  the records of the running context's store, asked per run

        `records_of` is asked only when no `store` was given."""
        self._given: IdempotencyRecords | None = (
            None
            if store is None
            else store if isinstance(store, IdempotencyRecords) else KeyValueRecords(store)
        )
        self._records_of = records_of or _records_here
        self.namespace = namespace
        self.observer: CacheObserver = observer or traced_and_measured(IDEMPOTENCY_OUTCOMES)
        self._declared: WeakKeyDictionary[type, IdempotencyPolicy] = WeakKeyDictionary()
        """Every class that declared `once()`, with its policy — what `policies()` describes."""

    @property
    def records(self) -> IdempotencyRecords:
        """The records given, else the context's — resolved at every run, so an `Idempotency`
        built at import follows where its context is kept."""
        return self._given if self._given is not None else self._records_of()

    def _key(self, key: Any) -> str:
        """The record key of `key`: hashed, under the namespace — two bounded contexts on one
        store never share a record.

            _key(("issue", "r-1"))  ->  "idempotency:billing:<hash>"    (namespace "billing")
        """
        prefix = f"idempotency:{self.namespace}:" if self.namespace else "idempotency:"
        return prefix + key_of(key)

    def _observe(self, outcome: IdempotencyOutcome) -> None:
        """Tell the observer how a run went — an observer that fails never fails the write."""
        try:
            self.observer.observed(outcome, self.namespace)
        except Exception:
            return

    # Running

    def _run_again[T](
        self,
        records: IdempotencyRecords,
        hashed: str,
        run: Callable[[], T],
        held: IdempotencyRecord,
        policy: IdempotencyPolicy,
        codec: Codec[T],
        error: Exception,
    ) -> T:
        """The kept answer no longer reads as what the use case answers — a deploy changed its
        shape: run again, said, and keep the new answer in its place."""
        logger.warning(
            f"the answer kept under idempotency key {hashed} no longer fits the use case's "
            f"response and it runs again: {error}"
        )
        with _running_as(hashed):
            answer = run()
        records.complete(
            hashed,
            held.model_copy(update={"answer": codec.encode(answer)}),
            policy.expires_after,
        )
        return answer

    def _replayed_before[T](
        self,
        records: IdempotencyRecords,
        before: Any,
        fingerprint: str,
        codec: Codec[T],
    ) -> tuple[bool, T | None]:
        """An answer completed under the key a release before this one wrote — in this
        namespace, or in none, where an `Idempotency(store)` of that release kept it —
        replayed, so a retry that crosses the deploy never runs the write twice. Nothing
        otherwise."""
        written = dict.fromkeys((self._key(before), "idempotency:" + key_of(before)))
        found = (records.read(one) for one in written)
        held = next((one for one in found if one is not None), None)
        if (
            held is None
            or held.state != RecordState.COMPLETED
            or held.fingerprint != fingerprint
        ):
            return False, None
        try:
            answer = codec.decode(held.answer)
        except Exception:
            return False, None
        self._observe(IdempotencyOutcome.REPLAYED)
        return True, answer

    def run[T](
        self,
        key: Any,
        run: Callable[[], T],
        policy: IdempotencyPolicy,
        codec: Codec[T],
        payload: Any = None,
        before: Any = None,
    ) -> T:
        """`run()` once for `key` — its parameters, hashed — and its answer for every repeat.
        `payload` is what `KeyReused` compares; `None` compares nothing. `before` is the key a
        previous release wrote for the same request: an answer completed there is replayed.
        See the module's context for why it claims first.

        1. `before` given and an answer completed there for this payload: that answer.
        2. The claim won: `run()` inside the records' transaction, knowing its key; its answer
           completed for `expires_after`. Anything raised releases the claim and is re-raised.
        3. The key held for another payload: `KeyReused`.
        4. The key held by a completed answer: that answer — run again when it no longer
           decodes as the use case's response.
        5. The key held by a run in flight: polled until `wait_for_completion` passes, then
           `AlreadyInProgress`.
        Final: the answer, run once or replayed.

            idempotency.run(("issue", "r-1"), issue, policy, codec, payload=command)
                ->  issue()'s answer — the first time, run and completed
                ->  the same answer, replayed — any repeat within `expires_after`
            ... payload=other_command      ->  KeyReused, the key held for another payload
            ... while the first still runs ->  AlreadyInProgress, after `wait_for_completion`

        Each outcome is told to the observer: claimed, replayed, key reused, in progress.
        """
        hashed = self._key(key)
        records = self.records
        fingerprint = "" if payload is None else key_of(payload)
        if before is not None:
            replayed, answer = self._replayed_before(records, before, fingerprint, codec)
            if replayed:
                return cast(T, answer)
        owner = uuid.uuid4().hex
        claim = IdempotencyRecord(
            state=RecordState.IN_PROGRESS, fingerprint=fingerprint, owner=owner
        )
        deadline = time.monotonic() + policy.wait_for_completion.total_seconds()
        while True:
            if records.claim(hashed, claim, policy.in_progress_for):
                self._observe(IdempotencyOutcome.CLAIMED)
                try:
                    with records.transaction():
                        with _running_as(hashed):
                            answer = run()
                        completed = {
                            "state": RecordState.COMPLETED,
                            "answer": codec.encode(answer),
                        }
                        records.complete(
                            hashed, claim.model_copy(update=completed), policy.expires_after
                        )
                except BaseException:
                    _released(records, hashed, owner)
                    raise
                return answer
            held = records.read(hashed)
            if held is None:
                continue
            if held.fingerprint != fingerprint:
                self._observe(IdempotencyOutcome.KEY_REUSED)
                raise exceptions.KeyReused(
                    "this idempotency key was already used for a different payload"
                )
            if held.state == RecordState.COMPLETED:
                try:
                    answer = codec.decode(held.answer)
                except Exception as error:
                    return self._run_again(records, hashed, run, held, policy, codec, error)
                self._observe(IdempotencyOutcome.REPLAYED)
                return answer
            if time.monotonic() >= deadline:
                self._observe(IdempotencyOutcome.IN_PROGRESS)
                raise exceptions.AlreadyInProgress(
                    "a run with this idempotency key is still in progress"
                )
            time.sleep(POLL_SECONDS)

    # Declaring

    def once(
        self,
        expires_after: timedelta,
        in_progress_for: timedelta = timedelta(minutes=1),
        wait_for_completion: timedelta = timedelta(0),
        vary_by: str | Sequence[str] = (),
    ):
        """Run the decorated Feature or ApplicationService once per key — see
        `IdempotencyPolicy` for each knob. The key is the Command's identity
        (`registered_name`), what its `idempotency_key()` answers (else the fields it sets apart
        from their defaults) and whose it is — the context's tenant and user plus the keys in
        `vary_by` (`owner_of`). The Command and the `vary_by` values are the payload
        `KeyReused` compares — so a field added with a default keeps a retry on its key.

            @billing.idempotency.once(timedelta(hours=24), vary_by="pos_id")

        1. The knobs kept as one `IdempotencyPolicy`.
        2. The class decorator it answers:
           2.1 a class that already declared `once()`, or whose `execute` is async, is
               refused (`ProgrammingError`);
           2.2 `execute` is replaced by a wrapper that, at each run, reads the response's
               codec (the first run only), the context's `vary_by` values, the Command's
               identity, its request key (`request_key_of`, a transport's key beside it) and
               its owner (`owner_of`);
           2.3 the wrapper calls `run` keyed `(command, request, owner)`, comparing
               `(dto, vary)`, replaying what a release before kept under
               `(command, request, vary)`.
        Final: the class itself, its policy kept for `policies()`.
        """
        policy = IdempotencyPolicy(
            expires_after=expires_after,
            in_progress_for=in_progress_for,
            wait_for_completion=wait_for_completion,
            vary_by=vary_by,
        )

        def declared[T: type](cls: T) -> T:
            """1. Refused twice on one class, and on an async `execute` — the bus refuses one
               too: an async caller reaches the use case through `get_async_bus()`, which runs
               this synchronous `execute`, claim first, on a worker thread.
            2. Final: `execute` replaced by one that runs the original once per key; the
               answer's codec is read from its return type at the first run.
            """
            if cls in self._declared:
                raise exceptions.ProgrammingError(
                    f"{cls.__name__} declares once() twice: declare one"
                )
            original = cls.execute  # type: ignore[attr-defined]
            if inspect.iscoroutinefunction(original):
                raise exceptions.ProgrammingError(
                    f"{cls.__name__}.execute is async: once() runs synchronous use cases — "
                    "declare it def execute, and reach it from async code with "
                    "bus.get_async_bus()"
                )
            codecs: list[Codec[Any]] = []

            @functools.wraps(original)
            def execute(handler: Any, dto: Any) -> Any:
                """The original `execute`, once per key.

                1. The answer's codec, read from the declared response type at the first run.
                2. The key: the Command's identity, its request key, its owner.
                Final: `run(...)` — with the key a release before this one wrote as `before`.
                """
                if not codecs:
                    codecs.append(JsonCodec(_response_of(cls, original)))
                context = handler.context or {}
                vary = {one: context.get(one) for one in policy.vary_by}
                running = current_execution()
                bus = running.bus if running is not None else ""
                command = registered_name(type(dto), bus)
                request = request_key_of(dto, context.get(IDEMPOTENCY_KEY))
                owner = owner_of(context, (*BY_TENANT_AND_USER, *policy.vary_by))
                return self.run(
                    (command, request, owner),
                    lambda: original(handler, dto),
                    policy,
                    codecs[0],
                    payload=(dto, vary),
                    before=(command, request, vary),
                )

            cls.execute = execute  # type: ignore[attr-defined]
            self._declared[cls] = policy
            _once_wrappers[execute] = policy
            return cls

        return declared

    def policies(self) -> dict[str, IdempotencyPolicy]:
        """Each class that declared `once()`, by its full name, with its policy."""
        return {_qualified(cls): policy for cls, policy in self._declared.items()}
