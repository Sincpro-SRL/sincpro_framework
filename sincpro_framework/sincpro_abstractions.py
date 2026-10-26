"""The pieces a bounded context is written with: `DataTransferObject`, `Feature`,
`ApplicationService` — and `Bus`, what every bus that runs them answers to.

    class CommandIssueInvoice(DataTransferObject):
        amount: Decimal

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice: ...

    billing(CommandIssueInvoice(amount=10), ResponseIssueInvoice)  ->  ResponseIssueInvoice
    bus.thread_context().execute                                  a thread pool's task
    await bus.get_async_bus()(dto)                                from async code

A bounded context never subclasses `Feature` or `ApplicationService` straight from here: its
`framework.py` wraps each once with the dependencies it registers (see the classes).
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import copy_context
from functools import partial
from typing import TYPE_CHECKING, Any, Generic, Type

from pydantic import BaseModel, ConfigDict
from typing_extensions import TypeVar

from .aio import AsyncBus

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from .bus import FeatureBus

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject | DataclassInstance")
TypeDTOResponse = TypeVar(
    "TypeDTOResponse", bound="DataTransferObject | DataclassInstance | list | dict"
)
ContextT = TypeVar("ContextT")


class DataTransferObject(BaseModel):
    """
    Abstraction that represent a object that will travel through to any layer
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        use_attribute_docstrings=True,
    )


class Bus(ABC):
    """What every bus is: something that executes a DTO — the `UseFramework`, the
    `FrameworkBus`, a layer bus, the `feature_bus` an ApplicationService holds. It brings the
    two ways to call it off the calling thread: `thread_context()` and `get_async_bus()`."""

    log_after_execution: bool = True

    @abstractmethod
    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """
        Main method to execute a DTO.
        If return_type is provided, returns an instance of return_type.
        Otherwise, returns None.
        """

    def thread_context(self) -> "Bus":
        """This bus, bound to the context of the calling thread — for a pool the project owns.

        Context: the node in play lives in a `ContextVar`, and a `ThreadPoolExecutor` hands a
        worker none of it: a plain `executor.submit(bus.execute, dto)` runs with an empty
        `self.context`. The bus answered runs every `execute` in a copy of the context taken here
        (as `in_context` does), whichever thread calls it.

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self.feature_bus.thread_context().execute, dto, return_type)
                    for dto in dtos                      one per task
                ]

        Call it **once per task you submit**, not once for a whole batch — a copied context can
        be entered by one thread at a time, so one handle shared by concurrent workers raises
        `RuntimeError`. `ContextExecutor` takes the copy per task on its own.
        """
        return ContextBoundBus(partial(copy_context().run, self.execute))

    def get_async_bus(self) -> "AsyncBus":
        """Return a stateless async facade bound to this bus.

        Unlike ``thread_context()``, this handle is reusable across concurrent
        calls: call ``get_async_bus()`` once, then ``await``/``asyncio.gather``
        many ``.execute()``/``__call__`` calls on it — each call captures its
        own context snapshot internally. Use this from a caller that is itself
        ``async def`` and wants to fan out several DTOs concurrently without
        blocking its event loop.
        """
        return AsyncBus(self.execute)


class ContextBoundBus(Bus):
    """`bus.thread_context()`: a bus whose every `execute` runs in the context it was bound to."""

    def __init__(self, execute: Callable[..., Any]) -> None:
        """Hold the bus's `execute`, already wrapped to run in the captured context."""
        self._execute = execute

    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """The bound bus's answer to `dto`, computed in the captured context."""
        return self._execute(dto, return_type)


class ContextConsumer:
    """`self.context` in a Feature or an ApplicationService: the `Context` of the execution in
    play, as its bus sees it — read and written like a mapping, navigated like `use_context()`
    (`self.context.entrypoint`, `.parent`).

    Context: the handler only holds the bus it was bound to and asks it, so this base knows
    nothing of how the context is built. Autocomplete of ``self.context`` is declared on the
    Feature stub as ``ContextT``, not here.
    """

    _context_binder: Any
    _context_fallback: dict

    def bind_to_framework(self, binder: Any) -> None:
        """Bind the handler to the bus it reads its context from — done once per bus
        generation, when the bus is built."""
        self._context_binder = binder

    @property
    def context(self) -> Any:
        """The bus's `Context` where the handler runs — a plain dict when no bus is bound yet
        (a handler built by hand, in a test)."""
        binder = self._context_binder
        if binder is not None:
            return binder._get_context()
        return self._context_fallback

    @context.setter
    def context(self, value: Any) -> None:
        """`self.context = {...}` replaces what the handler's scope says — on the bus's
        context when bound, on the fallback dict otherwise; `None` clears it.

            self.context = {"company": 3}   ->  self.context["company"]  ->  3
            self.context = None             ->  the handler's scope says nothing of its own

        The value is copied into a new dict first; a parent's keys are not touched."""
        mapping = dict(value) if value is not None else {}
        binder = self._context_binder
        if binder is not None:
            binder._set_context(mapping)
            return
        self._context_fallback = mapping


class Feature(ContextConsumer, ABC, Generic[TypeDTO, TypeDTOResponse, ContextT]):
    """
    Feature is the first layer of the framework, it is the main abstraction to execute a business logic.

    Features are atomic operations that handle specific business use cases. They receive a DTO,
    execute business logic, and return a response DTO.

    Features automatically receive injected dependencies as attributes through the framework's
    dependency injection system. These dependencies can be accessed via self.dependency_name.

    One instance is built per generation of its UseFramework and serves every execution, on
    every thread: hold request data in local variables, never on ``self``. ``self.context`` is
    per execution.

    Every bounded context wraps it once, in its ``framework.py``, with the dependencies it
    registers — and its Features inherit that base, never this one:

        # framework.py
        from sincpro_framework import Feature as _Feature

        class Feature(_Feature, DependencyContextType):
            ...                                         # the base of this bounded context

        # services/issue_invoice.py
        @billing.feature(CommandIssueInvoice)
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
                tenant_id = self.context.get("tenant_id")       # this execution's context
                number = self.numbering.next(tenant_id)         # a typed dependency
                return ResponseIssueInvoice(number=number)

    ``self.context`` is typed with the third parameter — ``Feature[Command, Response, Context]``;
    the parameters are given all three or none.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize the Feature. Dependencies are injected automatically by the framework.
        """
        self._context_binder = None
        self._context_fallback: dict = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """The use case itself: the business rule for one DTO, answered with a response DTO —
        or `None` for a command that answers nothing.

        Context: the bus calls it with everything around already done — context, span,
        interceptors; what it raises is the use case's failure, classified and reported by the
        bus — raise a `ClientError` subclass for an answer (not found, refused, invalid) and
        let the error handlers map it, rather than catching to answer.

            issue_invoice.execute(CommandIssueInvoice(amount=10))  ->  ResponseIssueInvoice
        """


class ApplicationService(ContextConsumer, ABC, Generic[TypeDTO, TypeDTOResponse, ContextT]):
    """
    Second layer of the framework, orchestration of features.

    ApplicationServices coordinate multiple Features to accomplish complex business workflows.
    They have access to all injected dependencies (same as Features) plus an exclusive
    feature_bus for executing other Features.

    Like a Feature, one instance serves every execution of a generation of the bus: request data
    stays in local variables, never on ``self``.

    ApplicationServices are ideal for:
    - Non-atomic operations requiring multiple steps
    - Coordinating between different Features
    - Complex business workflows with multiple decision points
    - Aggregating data from multiple sources

    Every bounded context wraps it once, in its ``framework.py``, as it wraps ``Feature``:

        # framework.py
        from sincpro_framework import ApplicationService as _ApplicationService

        class ApplicationService(_ApplicationService, DependencyContextType):
            ...                                         # the base of this bounded context

        # services/checkout.py
        @shop.app_service(CommandCheckout)
        class Checkout(ApplicationService):
            def execute(self, dto: CommandCheckout) -> ResponseCheckout:
                reserved = self.feature_bus(CommandReserve(sku=dto.sku), ResponseReserve)
                return ResponseCheckout(total=reserved.quantity * self.prices.of(dto.sku))
    """

    feature_bus: "FeatureBus"

    def __init__(self, feature_bus: "FeatureBus", *args, **kwargs):
        """
        Initialize the ApplicationService with feature_bus for orchestration.
        Additional dependencies are injected automatically by the framework.
        """
        self.feature_bus = feature_bus
        self._context_binder = None
        self._context_fallback: dict = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """The orchestration for one DTO: features run through `self.feature_bus`, their
        answers combined into a response DTO — or `None` for a command that answers nothing.

        Context: each feature it runs is its own execution under this one — its own span, node
        and error handling — so a failure in one names the chain it crossed.
        """
