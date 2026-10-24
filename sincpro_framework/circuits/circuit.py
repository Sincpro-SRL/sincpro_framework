"""`Circuit`: stop calling a dependency that keeps failing, and try it again once it may be back.

    siat = Circuit("siat")                          5 failures in a row open it for 30 seconds

    with siat:                                      a block
        answer = client.send(invoice)

    @siat                                           a function, a method, a coroutine, a
    def send(invoice): ...                          generator — guarded while it is iterated

    siat.call(client.send, invoice)                 a call

    SiatClient(circuit=Circuit("siat"))             an adapter that takes it — the usual way

While it is open a call fails at once with `CircuitOpen` — `UNAVAILABLE` on every wire, with the
seconds left as `retry_after`, never under a second — instead of waiting out a timeout that ties
up every thread. When that time passes one call is let through to try; it closes the circuit or
opens it again, and the others are told to come back in a second.

**Where its state lives is the context's.** A circuit is built once, with the adapter, before any
execution runs; at every call it reads where the running context keeps its leases — the process's
memory, or the Redis, Valkey, Memcached or DynamoDB every replica shares — so replicas that share a
store open and close together, and nothing about the store is said here. `store=` names one for a
test or a script.

**What counts as a failure is the classification's.** The dependency not answering, an outcome
unknown, a quota exhausted, or an error it never declared (a crash of the client) counts; an error
that is an answer — invalid, not found, a rule of the business, a conflict — does not: the
dependency is up, it said no. `counts=` says otherwise. A call that never ended — cancelled,
interrupted, a generator abandoned — is neither: nothing is counted, nothing cleared.

**A store that does not answer never blocks a call.** The circuit lets the calls through
unguarded for a while and tries the store again: the dependency is what it protects, not the
store.

How it sits on the store, so every store has it — memory, Redis, Valkey, Memcached, DynamoDB:

    open      a key written with the time it is open for: it lapses by itself into half-open
    tripped   a key that outlives `open`: open gone and tripped there is half-open
    probe     the one call let through half-open is whoever wins an `add`, held `probe_for`
    failures  a counter: failures in a row, cleared by a call that answers — in a row as the
              calls admitted with failures counted end; under concurrency, roughly
"""

import functools
import inspect
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, TypeVar, cast

from sincpro_framework import exceptions
from sincpro_framework.common.failures import refined_failure_kind
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.store import KeyValueStore, StoreUnavailable
from sincpro_framework.store.entrypoint.resolution import current_store, key_value_store_of

FAILURES = 5
"""Consecutive failures that open a circuit, unless it says."""
OPEN_FOR = timedelta(seconds=30)
"""How long a circuit stays open before it lets one call try, unless it says."""
TRIPPED_FOR = 10
"""How many times `open_for` a circuit remembers it opened — what half-open is told apart by."""
STORE_BYPASS = timedelta(seconds=30)
"""How long calls go through unguarded after the store did not answer."""
LEAST_RETRY_AFTER = timedelta(seconds=1)
"""The least `retry_after` a refused call is told — what a call half-open is told while one
tries, and the floor of a time left that clocks or whole-second TTLs bring to nothing."""

COUNTED = frozenset(
    {
        exceptions.FailureKind.UNAVAILABLE,
        exceptions.FailureKind.UNKNOWN_OUTCOME,
        exceptions.FailureKind.EXHAUSTED,
        exceptions.FailureKind.INTERNAL,
    }
)
"""The kinds a circuit counts by default: the dependency did not answer, may not have, asked to be
left alone, or failed in a way nobody declared. Every other kind is an answer."""

F = TypeVar("F", bound=Callable[..., Any])


class CircuitState(StrEnum):
    """Where a circuit stands, as `Circuit.state()` reads it off the store — for a health
    page, a metric or a test; a call never needs it, `admit` decides on its own."""

    CLOSED = "closed"
    """Calls go through."""
    OPEN = "open"
    """Calls fail at once, until its time passes."""
    HALF_OPEN = "half_open"
    """Its time passed: one call goes through to try; the others still fail at once."""


class CircuitOpen(exceptions.ServiceUnavailableError):
    """The dependency the circuit guards failed too often: the call was not made. Try again after
    `retry_after` — a second at least."""

    name: str
    retry_after: timedelta
    """Declared, so it is a `timedelta` again once rebuilt across a remote boundary."""

    def __init__(self, name: str, retry_after: timedelta) -> None:
        """Refused with the time left floored at `LEAST_RETRY_AFTER` — a clock or a
        whole-second TTL that rounds the time left to nothing never tells a caller to retry at
        once.

            CircuitOpen("siat", timedelta(0)).retry_after   ->  timedelta(seconds=1)
        """
        retry_after = max(retry_after, LEAST_RETRY_AFTER)
        super().__init__(
            f"circuit {name} is open — the call was not made; "
            f"try again in {retry_after.total_seconds():.0f}s"
        )
        self.name = name
        self.retry_after = retry_after


def counted_by_default(error: BaseException) -> bool:
    """Whether a failure says the dependency is not answering (`COUNTED`), not what it answered."""
    return isinstance(error, Exception) and refined_failure_kind(error) in COUNTED


@dataclass(frozen=True)
class _Admission:
    """What `admit` learned of one call, for `settle` to act on once it ended."""

    unguarded: bool = False
    """The store did not answer: the call goes through and nothing is written of it."""
    probe: bool = False
    """The one call let through half-open: it closes or reopens the circuit."""
    failing: bool = False
    """Failures were counted before it: an answer clears them."""


_ADMITTED: ContextVar[tuple[tuple["Circuit", _Admission], ...]] = ContextVar(
    "circuit_admitted", default=()
)
"""The admissions of the `with` blocks the running code is in, each with its circuit, innermost
last — per thread and per task, so one circuit guards any number of calls at once."""


class Circuit:
    """A named breaker around one dependency — its state kept in a store, not the object.

    Context: built once with the adapter, shared by every thread, task and replica; what a
    call may do is read at the call (`admit`) and what its end means is written after it
    (`settle`), so the object itself holds only its settings and the store-bypass deadline.

        siat = Circuit("siat", failures=3, open_for=timedelta(minutes=1))
        siat.call(client.send, invoice)      ->  the answer, or `CircuitOpen` while it is open
        siat.state()                         ->  CircuitState.CLOSED
    """

    def __init__(
        self,
        name: str,
        failures: int = FAILURES,
        open_for: timedelta = OPEN_FOR,
        counts: Callable[[BaseException], bool] = counted_by_default,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:circuit",
        clock: Callable[[], float] = time.time,
        probe_for: timedelta | None = None,
    ) -> None:
        """`name` is the dependency — the same name is the same circuit for every caller sharing
        the store; `store` pins one instead of the context's; `clock` is seconds since the epoch,
        which a test moves by hand; `probe_for` is how long the call let through half-open keeps
        the others out — `open_for` unless said: give more than the longest call takes, or a
        probe still waiting lets a second one in.

            Circuit("siat", failures=3, open_for=timedelta(minutes=1))
                ->  probe_for 60s; its state in the context's store, "sincpro:circuit:siat:*"
            Circuit("siat", failures=0)
                ->  ProgrammingError("circuit siat: failures=0 would never close — ...")

        1. Refused at once (`ProgrammingError`): `failures` under 1, an `open_for` that is no
           time, a `probe_for` given that is no time.
        2. The settings kept; `probe_for` is `open_for` when not given.
        Final: no store is touched and nothing is bypassed yet — the store is read per call.
        """
        if failures < 1:
            raise exceptions.ProgrammingError(
                f"circuit {name}: failures={failures} would never close — give 1 or more"
            )
        if open_for <= timedelta(0):
            raise exceptions.ProgrammingError(
                f"circuit {name}: open_for={open_for} is no time at all — give a positive time"
            )
        if probe_for is not None and probe_for <= timedelta(0):
            raise exceptions.ProgrammingError(
                f"circuit {name}: probe_for={probe_for} is no time at all — give a positive time"
            )
        self.name = name
        self.failures = failures
        self.open_for = open_for
        self.probe_for = open_for if probe_for is None else probe_for
        self.counts = counts
        self.prefix = prefix
        self.clock = clock
        self._store = store
        self._unguarded_until = 0.0

    def _key(self, part: str) -> str:
        """The store key of one part of the state: `sincpro:circuit:siat:open`."""
        return f"{self.prefix}:{self.name}:{part}"

    def store(self) -> KeyValueStore:
        """Where the state is: the store given, else the `KeyValueStore` of the running
        context — the bus in play, else the process, else memory — read at every call, so a
        circuit built before any execution follows the bus it runs in."""
        if self._store is not None:
            return self._store
        return key_value_store_of(current_store())

    def _unanswered(self, error: StoreUnavailable) -> _Admission:
        """The store did not answer: calls go through unguarded for `STORE_BYPASS` — kept in
        this object, per process, so no call waits on the store meanwhile — and it is logged.
        """
        self._unguarded_until = self.clock() + STORE_BYPASS.total_seconds()
        logger.warning(
            f"circuit {self.name}: its store did not answer ({error}); calls go through "
            f"unguarded for {STORE_BYPASS.total_seconds():.0f}s"
        )
        return _Admission(unguarded=True)

    def _trip(self, store: KeyValueStore) -> None:
        """Open: `open` written with its time, `tripped` outliving it, the count and the probe
        spent."""
        until = self.clock() + self.open_for.total_seconds()
        store.set(self._key("open"), repr(until).encode(), self.open_for)
        store.set(self._key("tripped"), b"1", self.open_for * TRIPPED_FOR)
        store.delete(self._key("failures"))
        store.delete(self._key("probe"))
        logger.warning(
            f"circuit {self.name}: open for {self.open_for.total_seconds():.0f}s — calls fail at "
            "once until then"
        )

    def _close(self, store: KeyValueStore) -> None:
        """Closed by a probe that answered — unless another caller opened it again meanwhile (a
        probe that outlived `probe_for` answering after a second one failed): then it stays open,
        and half-open after, never closed with no call tried."""
        if store.get(self._key("open")) is not None:
            return
        store.delete(self._key("tripped"))
        store.delete(self._key("probe"))
        store.delete(self._key("failures"))
        logger.info(f"circuit {self.name}: closed — the dependency answered again")

    def admit(self) -> _Admission:
        """Whether a call may be made now.

            siat.admit()   ->  _Admission()                       # closed, nothing failing
            siat.admit()   ->  _Admission(failing=True)           # closed, 2 failures counted
            siat.admit()   ->  CircuitOpen("siat", 18s left)      # open
            siat.admit()   ->  _Admission(probe=True)             # half-open, won the probe
            siat.admit()   ->  CircuitOpen("siat", 1s)            # half-open, probe taken

        1. The store did not answer a moment ago: through, unguarded.
        2. Open: `CircuitOpen`, with the seconds it stays open — a second at least.
        3. Half-open: through only for whoever wins the probe, held `probe_for`; the others
           `CircuitOpen`, to come back in a second.
        Final: closed — through, remembering whether failures were being counted.
        """
        if self.clock() < self._unguarded_until:
            return _Admission(unguarded=True)
        store = self.store()
        try:
            opened, tripped, failing = store.get_many(
                [self._key("open"), self._key("tripped"), self._key("failures")]
            )
            if opened is not None:
                left = float(opened) - self.clock()
                raise CircuitOpen(self.name, timedelta(seconds=max(left, 0)))
            if tripped is not None:
                if not store.add(self._key("probe"), b"1", self.probe_for):
                    raise CircuitOpen(self.name, LEAST_RETRY_AFTER)
                return _Admission(probe=True)
        except StoreUnavailable as error:
            return self._unanswered(error)
        return _Admission(failing=failing is not None)

    def settle(self, admission: _Admission, error: BaseException | None) -> None:
        """What a call's end does to the circuit.

            siat.settle(_Admission(), SiatDown())         ->  failures 4 → 5: open for 30s
            siat.settle(_Admission(probe=True), None)     ->  closed — the dependency answered
            siat.settle(_Admission(failing=True), InvoiceNotFound())
                                                          ->  an answer: the count cleared
            siat.settle(_Admission(probe=True), KeyboardInterrupt())
                                                          ->  the probe let go, nothing else

        1. A failure that counts: half-open, it opens again; closed, one more — and at
           `failures`, it opens.
        2. A call that never ended — cancelled, interrupted, a generator closed early (a
           `BaseException` that is no `Exception` and does not count): neither an answer nor a
           failure; the probe it held, if any, is let go for the next call to try.
        3. An answer — or a failure that does not count, which is an answer too: half-open, it
           closes; closed, the count is cleared.
        Final: a store that does not answer changes nothing; the next calls go through — and
        nothing that goes wrong keeping the circuit ever fails the call, it is logged.
        """
        if admission.unguarded:
            return
        try:
            store = self.store()
            if error is not None and self.counts(error):
                if admission.probe or store.increment(self._key("failures")) >= self.failures:
                    self._trip(store)
            elif error is not None and not isinstance(error, Exception):
                if admission.probe:
                    store.delete(self._key("probe"))
            elif admission.probe:
                self._close(store)
            elif admission.failing:
                store.delete(self._key("failures"))
        except StoreUnavailable as unanswered:
            self._unanswered(unanswered)
        except Exception:
            logger.exception(f"circuit {self.name}: the call's end could not be kept")

    def state(self) -> CircuitState:
        """Where the circuit stands now — open while `open` lives, half-open while only
        `tripped` does, else closed. A read only: it admits nothing and, unlike a call, lets a
        store that does not answer raise.

            siat.state()   ->  CircuitState.CLOSED          # nothing written
            siat.state()   ->  CircuitState.OPEN            # 5 failures in a row, 10s ago
            siat.state()   ->  CircuitState.HALF_OPEN       # 31s after it opened

        One `get_many` of `open` and `tripped`; the failure count is not read."""
        store = self.store()
        opened, tripped = store.get_many([self._key("open"), self._key("tripped")])
        if opened is not None:
            return CircuitState.OPEN
        return CircuitState.HALF_OPEN if tripped is not None else CircuitState.CLOSED

    def reset(self) -> None:
        """Closed, whatever it was — what an operator does once the dependency is known back."""
        store = self.store()
        store.delete(self._key("open"))
        self._close(store)

    def call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """`function(*args, **kwargs)` made through the circuit — for a call that is not worth
        a decorator or a block.

            siat.call(client.send, invoice)   ->  what `send` answers

        1. Admitted, or `CircuitOpen` — and the function is not called.
        2. Its failure is settled and raised as it was.
        Final: its answer, settled as one.
        """
        admission = self.admit()
        try:
            answer = function(*args, **kwargs)
        except BaseException as error:
            self.settle(admission, error)
            raise
        self.settle(admission, None)
        return answer

    def __enter__(self) -> "Circuit":
        """A block admitted — or `CircuitOpen` before it runs; the admission is kept in the
        running context, innermost last, for `__exit__` to settle."""
        _ADMITTED.set((*_ADMITTED.get(), (self, self.admit())))
        return self

    def __exit__(self, kind: Any, error: BaseException | None, trace: Any) -> None:
        """The block's end, settled on the admission this circuit made for it.

        1. The innermost admission of this circuit in the running context — blocks of other
           circuits exited out of order keep theirs.
        2. None there — the block was entered in another context (an `ExitStack`, a dependency
           with `yield` resumed in another task): nothing is settled, it is logged, and the
           block's own error goes on as it was; a probe it held lapses after `probe_for`.
        Final: settled — and nothing the circuit does ever raises over what the block raised.
        """
        admitted = _ADMITTED.get()
        for at in range(len(admitted) - 1, -1, -1):
            circuit, admission = admitted[at]
            if circuit is self:
                _ADMITTED.set(admitted[:at] + admitted[at + 1 :])
                self.settle(admission, error)
                return
        logger.warning(
            f"circuit {self.name}: a block was left in another context than it entered — "
            "its call is not counted"
        )

    async def __aenter__(self) -> "Circuit":
        """`async with` — the same admission as `with`; the store is read synchronously."""
        return self.__enter__()

    async def __aexit__(self, kind: Any, error: BaseException | None, trace: Any) -> None:
        """`async with`'s end — settled as `__exit__` does."""
        self.__exit__(kind, error, trace)

    def _guarded_coroutine(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """A coroutine function guarded per await: admitted when the coroutine starts running,
        not when it is created, and settled when it answers or fails."""

        @functools.wraps(function)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            admission = self.admit()
            try:
                answer = await function(*args, **kwargs)
            except BaseException as error:
                self.settle(admission, error)
                raise
            self.settle(admission, None)
            return answer

        return guarded

    def _guarded_generator(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """A generator guarded while it is iterated: admitted at its first `next`, settled when
        it ends — closed early, it was a call that never ended."""

        @functools.wraps(function)
        def guarded(*args: Any, **kwargs: Any) -> Generator[Any, Any, Any]:
            admission = self.admit()
            try:
                answer = yield from function(*args, **kwargs)
            except BaseException as error:
                self.settle(admission, error)
                raise
            self.settle(admission, None)
            return answer

        return guarded

    def _guarded_async_generator(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """An async generator guarded while it is iterated, what is sent and thrown into it
        passed on.

        1. Admitted at its first `__anext__`.
        2. Each value yielded; what is sent in is sent on, what is thrown in thrown on, and a
           close closes it.
        Final: settled when it ends, fails, or is closed early — a call that never ended.
        """

        @functools.wraps(function)
        async def guarded(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, Any]:
            admission = self.admit()
            inner = function(*args, **kwargs)
            try:
                value = await inner.__anext__()
                while True:
                    try:
                        sent = yield value
                    except GeneratorExit:
                        await inner.aclose()
                        raise
                    except BaseException as thrown:
                        value = await inner.athrow(thrown)
                    else:
                        value = await inner.asend(sent)
            except StopAsyncIteration:
                self.settle(admission, None)
            except BaseException as error:
                self.settle(admission, error)
                raise

        return guarded

    def __call__(self, function: F) -> F:
        """`function` guarded by this circuit — a function, a method, a coroutine, or a generator
        (sync or async), guarded while it is iterated."""
        if inspect.isasyncgenfunction(function):
            return cast(F, self._guarded_async_generator(function))
        if inspect.isgeneratorfunction(function):
            return cast(F, self._guarded_generator(function))
        if inspect.iscoroutinefunction(function):
            return cast(F, self._guarded_coroutine(function))

        @functools.wraps(function)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            return self.call(function, *args, **kwargs)

        return cast(F, guarded)
