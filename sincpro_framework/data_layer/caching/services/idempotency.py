"""`Idempotency`: a write run once per key, across replicas — a retry gets the first answer.

    class CommandIssueInvoice(DataTransferObject):
        request_id: str
        total: int

        def idempotency_key(self) -> str:               # the Command says what identifies it
            return self.request_id

    @billing.feature(CommandIssueInvoice)
    @billing.idempotency                                # where the context is stored: nothing built
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

1. The claim is won: the write runs, knowing its key (`Idempotency.current_key()`, to hand on to
   a system that deduplicates too). Its answer is stored for `expires_after` and replayed to every
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

A dry run — a `DryRunnable` Command with `dry_run=True` — is never claimed: it writes nothing,
so there is nothing to run once, and it answers with the store down. A run that answers a
`Preview` all the same — a confirmed write whose document changed since it was seen, asking again
— wrote nothing either: its claim is released and it is never replayed.

`once(...)` wraps the use case's own `execute`, so nothing is wired on the bus: the use case still
runs inside it — Sentry, its span, and the access guard around it, so a replayed answer is still
authorized. A subclass that inherits `execute` inherits `once()`; a `replaces=` handler with an
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
from typing import Any, ClassVar, cast, get_type_hints
from weakref import WeakKeyDictionary

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import Owner, context_executions, use_context
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.caching.adapters.json_codec import JsonCodec
from sincpro_framework.data_layer.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.data_layer.caching.adapters.keys import KeyEncoder
from sincpro_framework.data_layer.caching.adapters.observers import (
    IDEMPOTENCY_OUTCOMES,
    Observers,
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
    IdempotentCommand,
)
from sincpro_framework.data_layer.caching.domain.observer import (
    CacheObserver,
    IdempotencyOutcome,
)
from sincpro_framework.data_layer.caching.domain.policies import IdempotencyPolicy
from sincpro_framework.data_layer.caching.infrastructure.flight import POLL_SECONDS
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.observability.metrics import metrics
from sincpro_framework.sincpro_abstractions import DryRunnable, Preview
from sincpro_framework.sincpro_logger import logger


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


def _response_of(handler: type, execute: Callable[..., Any]) -> Any:
    """The response type `execute` declares — what a stored answer is decoded back into for a
    replay; one that declares none is a mistake: `ProgrammingError`, saying to declare it."""
    response = get_type_hints(execute).get("return")
    if response is None:
        raise exceptions.ProgrammingError(
            f"{handler.__name__}.execute declares no response type: declare it, so a stored "
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

    _running: ClassVar[ContextVar[str | None]] = ContextVar(
        "sincpro_idempotency_key", default=None
    )
    """The key of the idempotent run in progress in this thread or task — `current_key()`."""

    _watched: ClassVar[ContextVar[list[IdempotencyOutcome] | None]] = ContextVar(
        "sincpro_idempotency_outcomes", default=None
    )
    """The list an `outcomes()` block collects into — handed down, so a run on a worker thread
    appends to the very list the caller reads."""

    _once_wrappers: ClassVar[WeakKeyDictionary[Callable[..., Any], IdempotencyPolicy]] = (
        WeakKeyDictionary()
    )
    """Every `execute` that `once()` wrote, on any `Idempotency`, with its policy — what
    `declared_by` looks up."""

    def __init__(
        self,
        store: KeyValueStore | IdempotencyRecords | None = None,
        namespace: str = "",
        observer: CacheObserver | None = None,
        records_of: Callable[[], IdempotencyRecords] | None = None,
    ) -> None:
        """`store` is what replicas share: a `KeyValueStore` (records on it, best effort for a
        crash mid-write) or `IdempotencyRecords` of your own (transactional, exactly once).
        With none, the records are where the context is stored — `records_of` says which, asked
        at every run (a bus's facade passes its own); without it, the running context's.
        `namespace` separates these keys from others in the same store.

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
        self.observer: CacheObserver = observer or Observers.traced_and_measured(
            IDEMPOTENCY_OUTCOMES
        )
        self._declared: WeakKeyDictionary[type, IdempotencyPolicy] = WeakKeyDictionary()
        """Every class that declared `once()`, with its policy — what `policies()` describes."""

    @classmethod
    def current_key(cls) -> str | None:
        """The key of the idempotent run in progress — `None` outside one. Hand it on (derived
        with `KeyEncoder.key_of(Idempotency.current_key(), "payments")`) to another system that
        deduplicates by key, so a write that ran twice after a crash is still one write there.
        """
        return cls._running.get()

    @classmethod
    @contextmanager
    def _running_as(cls, key: str) -> Generator[None, None, None]:
        """`key` is the idempotent run in progress for the block — what `current_key()`
        answers inside the write; the previous one comes back when it ends."""
        token = cls._running.set(key)
        try:
            yield
        finally:
            cls._running.reset(token)

    @classmethod
    @contextmanager
    def outcomes(cls) -> Generator[list[IdempotencyOutcome], None, None]:
        """What every idempotent run inside the block did, in order — what a caller reads to
        tell its client that nothing was written again.

            with billing.idempotency.outcomes() as seen:
                billing(CommandCharge(request_id="r-1", amount=10))
            seen   ->  [IdempotencyOutcome.CLAIMED]      the charge ran
            ...    ->  [IdempotencyOutcome.REPLAYED]     a retry: the first answer, nothing run

        Context: a list handed down rather than a value set back — a use case reached through
        `get_async_bus()` runs on a worker thread, and appends to this same list. A block inside
        another collects its own runs; the outer one does not see them."""
        seen: list[IdempotencyOutcome] = []
        token = cls._watched.set(seen)
        try:
            yield seen
        finally:
            cls._watched.reset(token)

    @classmethod
    def declared_by(cls, handler: type) -> bool:
        """Whether `handler` runs once per key: its `execute`, as Python resolves it, is one
        `once()` wrote — on any `Idempotency`. A subclass that inherits `execute` answers
        `True`; a replacement with an `execute` of its own answers `False` until it declares
        `once()` itself.

            Idempotency.declared_by(IssueInvoice)   ->  True   (`@billing.idempotency`)
            Idempotency.declared_by(int)            ->  False

        Context: an entrypoint that exposes use cases (a retry-safe flag, an `Idempotency-Key`
        header) asks this instead of reading how the method happens to be named."""
        execute = getattr(handler, "execute", None)
        return execute is not None and execute in cls._once_wrappers

    @property
    def records(self) -> IdempotencyRecords:
        """The records given, else the context's — resolved at every run, so an `Idempotency`
        built at import follows where its context is stored."""
        return self._given if self._given is not None else self._records_of()

    def _key(self, key: Any) -> str:
        """The record key of `key`: hashed, under the namespace — two bounded contexts on one
        store never share a record.

            _key(("issue", "r-1"))  ->  "idempotency:billing:<hash>"    (namespace "billing")
        """
        prefix = f"idempotency:{self.namespace}:" if self.namespace else "idempotency:"
        return prefix + KeyEncoder.key_of(key)

    def _observe(self, outcome: IdempotencyOutcome) -> None:
        """Tell the `outcomes()` block in play, the run being measured — a replay records no
        business metric — and the observer how a run went; an observer that fails never fails
        the write."""
        watched = self._watched.get()
        if watched is not None:
            watched.append(outcome)
        if outcome is IdempotencyOutcome.REPLAYED:
            metrics.replayed()
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
        """The stored answer no longer reads as what the use case answers — a deploy changed its
        shape: run again, said, and store the new answer in its place."""
        logger.warning(
            f"the answer stored under idempotency key {hashed} no longer fits the use case's "
            f"response and it runs again: {error}"
        )
        with self._running_as(hashed):
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
        namespace, or in none, where an `Idempotency(store)` of that release stored it —
        replayed, so a retry that crosses the deploy never runs the write twice. Nothing
        otherwise."""
        written = dict.fromkeys(
            (self._key(before), "idempotency:" + KeyEncoder.key_of(before))
        )
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
           completed for `expires_after`. Anything raised releases the claim and is re-raised;
           an answer that is a `Preview` — nothing was done, what would be is told again —
           releases it too, and is never replayed.
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
        fingerprint = "" if payload is None else KeyEncoder.key_of(payload)
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
                        with self._running_as(hashed):
                            answer = run()
                        if not isinstance(answer, Preview):
                            completed = {
                                "state": RecordState.COMPLETED,
                                "answer": codec.encode(answer),
                            }
                            records.complete(
                                hashed,
                                claim.model_copy(update=completed),
                                policy.expires_after,
                            )
                except BaseException:
                    _released(records, hashed, owner)
                    raise
                if isinstance(answer, Preview):
                    _released(records, hashed, owner)
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

    def completed_key(self, key: Any) -> bool:
        """Whether `key`, as `run(key, ...)` was given it, holds a completed answer that has not
        expired — running it again would replay that answer.

            idempotency.run(("charge", "r-1"), charge, policy, codec)
            idempotency.completed_key(("charge", "r-1"))   ->  True
            idempotency.completed_key(("charge", "r-2"))   ->  False      never run, or expired
        """
        held = self.records.read(self._key(key))
        return held is not None and held.state == RecordState.COMPLETED

    # Declaring

    def request_key_of(self, command: Any, transport_key: Any = None) -> Any:
        """What identifies `command`: its own `idempotency_key()`; else the key its transport
        received; else the whole Command.

        Context: the Command stays authoritative — a header can never split what the Command
        says is one request. The payload is still compared whichever key wins, so a header
        reused for other arguments is `KeyReused`.

            request_key_of(CommandIssueInvoice(request_id="r1", total=10), "hdr-9")  ->  "r1"
            request_key_of(CommandRefund(amount=5), "hdr-9")  ->  ("idempotency_key", "hdr-9")
            request_key_of(CommandRefund(amount=5))           ->  CommandRefund(amount=5)

        The transport's key is tagged, so it never hashes like a Command whose own key is equal.
        """
        if isinstance(command, IdempotentCommand):
            return command.idempotency_key()
        if transport_key is not None:
            return (IDEMPOTENCY_KEY, transport_key)
        return command

    def _identity(
        self, dto: Any, bus: str, context: Any, vary_by: Sequence[str]
    ) -> tuple[Any, Any, Any]:
        """What a declared run of `dto` on `bus`, under `context`, is keyed by: its key, the
        payload `KeyReused` compares and the key a release before this one wrote.

        1. The `vary_by` values the context says.
        2. The Command's identity on the bus (`registered_name`), its request key
           (`request_key_of`, a transport's key beside it) and its owner (`Owner.of`).
        Final: `((command, request, owner), (dto, vary), (command, request, vary))`.
        """
        vary = {one: context.get(one) for one in vary_by}
        command = registered_name(type(dto), bus)
        request = self.request_key_of(dto, context.get(IDEMPOTENCY_KEY))
        owner = Owner.of(context, (*Owner.BY_TENANT_AND_USER, *vary_by))
        return (command, request, owner), (dto, vary), (command, request, vary)

    def completed(
        self, dto: Any, bus: str | None = None, vary_by: str | Sequence[str] = ()
    ) -> bool:
        """Whether running `dto` now would replay an answer instead of writing — a run of the
        same request, by the same owner and with the same payload, completed and not expired.

            billing.idempotency.completed(CommandCharge(request_id="r-1", amount=10))
                ->  False      never run: confirming writes
                ->  True       ran: confirming again replays, nothing is written

        `bus` is the bounded context the use case runs on — the one in play when unsaid;
        `vary_by` the same keys the use case declared. Read from the context of the call, as
        the run itself would be: ask it inside the scope the run will open with."""
        context = use_context()
        if bus is None:
            running = context_executions.current()
            bus = running.bus if running is not None else ""
        varied = (vary_by,) if isinstance(vary_by, str) else tuple(vary_by)
        key, payload, _ = self._identity(dto, bus, context, varied)
        held = self.records.read(self._key(key))
        return (
            held is not None
            and held.state == RecordState.COMPLETED
            and held.fingerprint == KeyEncoder.key_of(payload)
        )

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
        `vary_by` (`Owner.of`). The Command and the `vary_by` values are the payload
        `KeyReused` compares — so a field added with a default still lets a retry match its key.

            @billing.idempotency.once(timedelta(hours=24), vary_by="pos_id")

        1. The knobs gathered as one `IdempotencyPolicy`.
        2. The class decorator it answers:
           2.1 a class that already declared `once()`, or whose `execute` is async, is
               refused (`ProgrammingError`);
           2.2 `execute` is replaced by a wrapper that runs a dry run (`DryRunnable`) as it
               is, and at each other run reads the response's
               codec (the first run only), the context's `vary_by` values, the Command's
               identity, its request key (`request_key_of`, a transport's key beside it) and
               its owner (`Owner.of`);
           2.3 the wrapper calls `run` keyed `(command, request, owner)`, comparing
               `(dto, vary)`, replaying what a release before stored under
               `(command, request, vary)`.
        Final: the class itself, its policy recorded for `policies()`.
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

                1. A dry run — a `DryRunnable` with `dry_run=True` — runs the original as it is:
                   nothing is claimed, stored or told, and the store is never asked.
                2. The answer's codec, read from the declared response type at the first run.
                3. The key: the Command's identity, its request key, its owner.
                Final: `run(...)` — with the key a release before this one wrote as `before`.
                """
                if isinstance(dto, DryRunnable) and dto.dry_run:
                    return original(handler, dto)
                if not codecs:
                    codecs.append(JsonCodec(_response_of(cls, original)))
                context = handler.context or {}
                running = context_executions.current()
                bus = running.bus if running is not None else ""
                key, payload, before = self._identity(dto, bus, context, policy.vary_by)
                return self.run(
                    key,
                    lambda: original(handler, dto),
                    policy,
                    codecs[0],
                    payload=payload,
                    before=before,
                )

            cls.execute = execute  # type: ignore[attr-defined]
            self._declared[cls] = policy
            self._once_wrappers[execute] = policy
            return cls

        return declared

    def policies(self) -> dict[str, IdempotencyPolicy]:
        """Each class that declared `once()`, by its full name, with its policy."""
        return {
            f"{cls.__module__}.{cls.__qualname__}": policy
            for cls, policy in self._declared.items()
        }
