"""Interceptors: code that runs around one use case, from outside it.

    @billing.interceptor(CommandCreateInvoice)
    def credit_check(dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]):
        if not credit.allows(dto.customer_id):
            raise NoCredit(dto.customer_id)          # veto: the handler never runs
        return call_next(dto)                        # the next interceptor, or the handler

Context: an interceptor may look, veto, adjust the Command or the response, or answer by itself,
but the Command it passes on and the response it hands back keep their class. The bus routes by
class, so a Command that changed class would be another use case wearing this one's name.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sincpro_framework.common.ordering import DEFAULT_SEQUENCE, Placement, name_of
from sincpro_framework.exceptions import InterceptorContractViolation

type CallNext[TResponse] = Callable[[Any], TResponse]
type Interceptor = Callable[[Any, CallNext[Any]], Any]


@dataclass(frozen=True)
class InterceptorRegistration:
    interceptor: Interceptor
    commands: tuple[type, ...]
    """Empty means every Command of the bus."""
    sequence: int = DEFAULT_SEQUENCE
    before: tuple[Interceptor, ...] = ()
    after: tuple[Interceptor, ...] = ()
    replaces: Interceptor | None = None

    @property
    def placement(self) -> Placement:
        return Placement(
            self.interceptor, self.sequence, self.before, self.after, self.replaces
        )

    @property
    def name(self) -> str:
        return name_of(self.interceptor)

    def wraps(self, command: type) -> bool:
        return not self.commands or command in self.commands


def interceptors_for(
    command: type, registrations: Sequence[InterceptorRegistration]
) -> tuple:
    return tuple(one.interceptor for one in registrations if one.wraps(command))


def run_around(
    interceptors: Sequence[Interceptor], handler: Callable[[Any], Any], dto: Any
) -> Any:
    """Context: the first interceptor is the outermost; the handler is the end.

    1. Each interceptor gets a `call_next` bound to the next position.
    2. What reaches `call_next` must be the class the call started with.
    3. Final: what an interceptor returns after calling `call_next` must be the class the next
       one answered with.
    """
    command = type(dto)

    def call_at(position: int, current: Any) -> Any:
        if position == len(interceptors):
            return handler(current)
        interceptor = interceptors[position]
        answered: list[Any] = []

        def call_next(next_dto: Any) -> Any:
            if type(next_dto) is not command:
                raise InterceptorContractViolation(
                    f"{name_of(interceptor)} passed {type(next_dto).__name__} on, but it "
                    f"intercepts {command.__name__}: adjust it with model_copy, keep its class"
                )
            response = call_at(position + 1, next_dto)
            answered.append(response)
            return response

        result = interceptor(current, call_next)
        if answered and answered[0] is not None and not isinstance(result, type(answered[0])):
            raise InterceptorContractViolation(
                f"{name_of(interceptor)} answered {type(result).__name__}, but "
                f"{command.__name__} answers {type(answered[0]).__name__}"
            )
        return result

    return call_at(0, dto)
