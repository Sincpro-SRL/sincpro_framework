"""Who hears what an execution announces: a bus's own listeners, and the process's.

Each kind of fact — a use case that begins, one that answered, a failure that escaped — has its own
`Listeners` on every bus, and one for the process that every bus of it also tells:

    billing.on_completion(audit)                    this bus
    completions.subscribe(audit)                    every bus of the process

**A listener observes.** It is a function, called in the thread of the execution, in the order it was
added. One that raises is logged and never reaches the execution nor the next listener: an execution
neither waits for its listeners nor knows whether they ran. Nothing here is retried or kept.

**It hears everything, unless told less** — the same three steps on every way to listen:

    billing.on_failure(alert)                                         every failure of the bus
    billing.on_failure(alert, use_cases=[CommandIssueInvoice])        the failures of these
    billing.on_failure(alert, when=Condition(field="context.tenant_id", value="acme"))

`when` is a filter of `sincpro_framework.ddd.criteria` — a `Condition`, `All`, `Any`, `Not`, its JSON,
or a list of them that must all hold — over the event's fields by path: `use_case`, `bus`, `kind`,
`dto.amount`, `context.tenant_id`, `response.total`. A path the event does not hold reads `None`.
"""

import threading
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.events import DomainEvent

type Listener = Callable[[Any], Any]
type Hears = Callable[["DomainEvent"], bool]
"""Whether a listener hears an event — `None` when it hears every one."""


def _value_at(event: "DomainEvent", path: str) -> Any:
    """What `event` holds at `path` — `kind`, `context.tenant_id`, `dto.amount` — `None` when it
    holds nothing there."""
    head, _, rest = path.partition(".")
    found: Any = getattr(event, head, None)
    for step in rest.split(".") if rest else ():
        found = found.get(step) if isinstance(found, Mapping) else None
    return found


def hearing(use_cases: Iterable[type | str] | None = None, when: Any = None) -> Hears | None:
    """What a listener hears: every event when nothing is said, else the events of `use_cases`
    that `when` holds for.

        in      [CommandIssueInvoice], None                →  billing.CommandIssueInvoice only
        in      None, Condition(field="kind", value="unavailable")  →  failures of that kind
        in      None, None                                  →  None: every event

    A use case is its Command or its name, `billing.CommandIssueInvoice`. `when` builds the
    filter once, here; the criteria vocabulary is imported only by whoever writes one.

    1. Neither said: `None`, every event heard.
    2. The use cases named, as names — a Command by its class name.
    3. `when` given:
       3.1 one filter, or a list of them;
       3.2 each one written as JSON read into an expression;
       3.3 one kept as it is, several joined under `All`.
    Final: `hears` — an event is heard when its use case is named, in full or by its last
    segment, and `when` holds over its fields.
    """
    if use_cases is None and when is None:
        return None
    names = (
        None
        if use_cases is None
        else frozenset(one if isinstance(one, str) else one.__name__ for one in use_cases)
    )
    holds_over: Callable[[Callable[[str], Any]], bool] | None = None
    if when is not None:
        from sincpro_framework.ddd.criteria import All, expression_from, holds

        written = [when] if not isinstance(when, list) else when
        parts = [expression_from(one) if isinstance(one, Mapping) else one for one in written]
        holds_over = partial(holds, parts[0] if len(parts) == 1 else All(all=parts))

    def hears(event: "DomainEvent") -> bool:
        """Whether this one event is heard: its use case named — in full or by its Command's
        name alone — and `when` holding over its fields."""
        use_case = getattr(event, "use_case", "")
        if names is not None and not {use_case, use_case.rsplit(".", 1)[-1]} & names:
            return False
        return holds_over is None or holds_over(partial(_value_at, event))

    return hears


class Listeners:
    """The listeners of one kind of fact: the ones added here, and the process's."""

    def __init__(self, process: "Listeners | None" = None) -> None:
        """`process` is the process-wide `Listeners` of the same kind of fact — every bus's
        listeners also tell it; `None` for the process's own."""
        self._own: list[tuple[Listener, Hears | None]] = []
        self._process = process
        self._lock = threading.Lock()

    def subscribe(
        self,
        listener: Listener,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Listener:
        """`listener` hears from now on — every event, or those of `use_cases` that `when` holds
        for (`hearing`). Usable as a decorator."""
        hears = hearing(use_cases, when)
        with self._lock:
            self._own.append((listener, hears))
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        """`listener` hears nothing more here — every time it was added; the process's
        listeners are not touched."""
        with self._lock:
            self._own = [one for one in self._own if one[0] != listener]

    def clear(self) -> None:
        """Nobody added here hears anything — what a test starts from."""
        with self._lock:
            self._own.clear()

    def heard(self) -> bool:
        """Whether anybody hears — read without the lock: it is the cost of every execution nobody
        listens to, so nothing is built for them."""
        return bool(self._own) or (self._process is not None and self._process.heard())

    def listening(self) -> list[tuple[Listener, Hears | None]]:
        """Who hears, and what: the process's first, then the ones added here — each once.

            process: [audit]; here: [alert, audit]    ->  [(audit, None), (alert, None)]

        A listener added both to the process and here is kept where it came first.
        """
        with self._lock:
            own = list(self._own)
        heard = [*(self._process.listening() if self._process is not None else []), *own]
        unique: list[tuple[Listener, Hears | None]] = []
        for one in heard:
            if all(one[0] != kept[0] for kept in unique):
                unique.append(one)
        return unique

    def announce(self, event: "DomainEvent") -> None:
        """`event` to each listener that hears it, in turn, never raising.

            announce(ExecutionFailed(use_case="billing.CommandPay", ...))
            ->  None; audit and alert called with it, a raising one logged and skipped

        A listener that raises is logged with its qualified name; the next one still hears.
        """
        for listener, hears in self.listening():
            try:
                if hears is None or hears(event):
                    listener(event)
            except Exception:  # noqa: BLE001 - a listener never reaches the execution
                logger.exception(
                    f"a listener of {event.name} failed: "
                    f"{getattr(listener, '__qualname__', listener)!r}"
                )


def to_publisher(publisher: Any) -> Listener:
    """A listener that hands each event to `publisher` — a queue, another bus, a broker."""

    def publish(event: "DomainEvent") -> None:
        publisher.publish(event)

    publish.__qualname__ = f"publish_to({type(publisher).__name__})"
    return publish
