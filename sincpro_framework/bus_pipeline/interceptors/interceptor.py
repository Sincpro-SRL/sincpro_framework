"""Interceptors: code that runs around one use case, from outside it.

    @billing.interceptor(CommandCreateInvoice)
    def credit_check(dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]):
        if not credit.allows(dto.customer_id):
            raise NoCredit(dto.customer_id)          # veto: the handler never runs
        return call_next(dto)                        # the next interceptor, or the handler

Context: an interceptor may look, veto, adjust the Command or the response, or answer by itself,
but the Command it passes on and the response it hands back preserve their class. The bus routes by
class, so a Command that changed class would be another use case wearing this one's name.
"""

from abc import abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.common.ordering import DEFAULT_SEQUENCE, Placement, name_of
from sincpro_framework.sincpro_abstractions import ContextConsumer

type CallNext[TResponse] = Callable[[Any], TResponse]
type InterceptorCall = Callable[[Any, CallNext[Any]], Any]
"""An interceptor as the chain runs it: `(dto, call_next)`."""


class Interceptor(ContextConsumer):
    """An interceptor written as a class — its dependencies injected as a Feature's are,
    `self.<name>`, and typed by the bounded context's own base:

        class Interceptor(_Interceptor, DependencyContextType): ...      # framework.py, once

        @billing.interceptor(CommandIssueInvoice)
        class CheckCredit(Interceptor):
            def intercept(self, dto: CommandIssueInvoice, call_next: CallNext[ResponseInvoice]):
                self.credit.check(dto.customer_id)      # injected, autocompleted
                return call_next(dto)

    Context: the bus makes one per generation, with that generation's dependencies and
    context, so a dependency replaced in a `fresh()` bus reaches it. `replaces=` and
    `without_interceptor` name the class. A function `(dto, call_next)` stays as it was.
    """

    def __init__(self) -> None:
        """No bus bound yet: the bus binds it, and sets its dependencies, when it is built."""
        self._context_binder = None
        self._context_fallback: dict = {}

    @abstractmethod
    def intercept(self, dto: Any, call_next: CallNext[Any]) -> Any:
        """Look, veto, adjust or answer — and `call_next(dto)` to go on."""


type Intercepting = InterceptorCall | type[Interceptor]
"""What a project registers: a function `(dto, call_next)`, or a class of `Interceptor`."""


@dataclass(frozen=True)
class InterceptorRegistration:
    """One `@bus.interceptor(...)` as it was asked: the function, the Commands it wraps and
    where it goes in the order — stored as asked, so the order is worked out again at each build
    with every interceptor registered by then."""

    interceptor: Intercepting
    commands: tuple[type, ...]
    """Empty means every Command of the bus."""
    sequence: int = DEFAULT_SEQUENCE
    before: tuple[Intercepting, ...] = ()
    after: tuple[Intercepting, ...] = ()
    replaces: Intercepting | None = None
    subclasses: bool = False
    """Whether it wraps every subclass of the Commands it names too — a base no handler answers
    (`WriteCommand`) standing for all of them."""

    @property
    def placement(self) -> Placement:
        """Its place in the order, as `common.ordering` reads it for every extension point."""
        return Placement(
            self.interceptor, self.sequence, self.before, self.after, self.replaces
        )

    @property
    def name(self) -> str:
        """The interceptor's name, for a log line or refusal: `billing.hooks.credit_check`."""
        return name_of(self.interceptor)

    def wraps(self, command: type) -> bool:
        """Whether it runs around `command` — every Command when it named none.

        InterceptorRegistration(audit, ()).wraps(CommandCreateInvoice)          ->  True
        InterceptorRegistration(check, (CommandPay,)).wraps(CommandCreateInvoice) ->  False
        InterceptorRegistration(check, (WriteCommand,), subclasses=True).wraps(CommandPay)
            ->  True, when CommandPay is a WriteCommand
        """
        if not self.commands or command in self.commands:
            return True
        return self.subclasses and issubclass(command, self.commands)


def interceptors_for(
    command: type,
    registrations: Sequence[InterceptorRegistration],
    calls: Mapping[Any, InterceptorCall],
) -> tuple[InterceptorCall, ...]:
    """The calls of the interceptors that wrap `command`, in the order of `registrations` —
    already ordered, outermost first; `calls` answers each registered item as the chain runs
    it (a class of `Interceptor`, its instance's `intercept`)."""
    return tuple(calls[one.interceptor] for one in registrations if one.wraps(command))


def run_around(
    interceptors: Sequence[InterceptorCall], handler: Callable[[Any], Any], dto: Any
) -> Any:
    """Run `handler` on `dto` wrapped by `interceptors`, each guarding the type it passes on.

    Context: the first interceptor is the outermost; the handler is the end.

        run_around((), feature.execute, CommandPay(amount=10))  ->  feature.execute's answer
        run_around((audit, retry), feature.execute, dto)        ->  audit(retry(execute))
        run_around((<passes CommandRefund on>,), feature.execute, CommandPay(...))
        ->  ProgrammingError: passed CommandRefund on, but it intercepts CommandPay

    1. Each interceptor gets a `call_next` bound to the next position.
    2. What reaches `call_next` must be the class the call started with.
    Final: what an interceptor returns after calling `call_next` must be the class the next
    one answered with — or anything, when the next one answered `None`.
    """
    command = type(dto)

    def call_at(position: int, current: Any) -> Any:
        """The chain from `position` inward, run on `current`.

        1. Past the last interceptor: the handler answers.
        2. The interceptor at `position` runs with a `call_next` that refuses a Command of
           another class, and records what the inner chain answered.
        Final: its result — refused when it called `call_next`, got an answer, and returned
        something of another class.
        """
        if position == len(interceptors):
            return handler(current)
        interceptor = interceptors[position]
        answered: list[Any] = []

        def call_next(next_dto: Any) -> Any:
            """The next position, on the Command as this interceptor passes it — same type."""
            if type(next_dto) is not command:
                raise exceptions.ProgrammingError(
                    f"{name_of(interceptor)} passed {type(next_dto).__name__} on, but it "
                    f"intercepts {command.__name__}: adjust it with model_copy, preserving its class"
                )
            response = call_at(position + 1, next_dto)
            answered.append(response)
            return response

        result = interceptor(current, call_next)
        if answered and answered[0] is not None and not isinstance(result, type(answered[0])):
            raise exceptions.ProgrammingError(
                f"{name_of(interceptor)} answered {type(result).__name__}, but "
                f"{command.__name__} answers {type(answered[0]).__name__}"
            )
        return result

    return call_at(0, dto)
