"""`HostedContextMixin`: what a `UseFramework` mixes in to stand for a bounded context another
service hosts — a reference that forwards every call — or to host its own for other services.

Context: the context map (`hosted_by` in the settings) is read when the bus is created; a bus a
process serves (`run_here`) runs here whatever the map says.
"""

from dataclasses import replace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Mapping,
    cast,
    get_type_hints,
    overload,
)

from pydantic import ValidationError
from sincpro_log.logger import LoggerProxy

from sincpro_framework.circuits import Circuit, CircuitOpen
from sincpro_framework.common.serialization import rebuilt
from sincpro_framework.common.transport.addresses import HostedAt
from sincpro_framework.context.infrastructure.tree import handed_on
from sincpro_framework.remote_execution.adapters.transport import circuit_for, transport_for
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.payload import Payload
from sincpro_framework.remote_execution.entrypoint.hosts import (
    Attach,
    OpenHost,
    serve_contexts,
)
from sincpro_framework.remote_execution.infrastructure.configuration import configured_host


class HostedContextMixin:
    """What a `UseFramework` adds to be reached where its context is hosted, or to host it."""

    _registrations: list[Callable[[Any], Any]]
    handler_of: Callable[[type], "type | None"]
    _inherited_context: Callable[[], dict[str, Any]]
    current_context: Callable[[], Any]

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

        @property
        def logger(self) -> LoggerProxy: ...

        @property
        def dto_registry(self) -> Mapping[str, type]: ...

    def _reach_at(self, hosted_at: HostedAt) -> None:
        """Point this bus at the service hosting it — said once in the logs, so a process's start
        shows every context it reaches elsewhere."""
        self._hosted_at = hosted_at
        self.logger.info(
            f"{self.name} is hosted at {hosted_at}: this bus forwards every call"
        )

    def _init_hosting(self, bundled_context_name: str) -> None:
        """Run here, no circuit said — and pointed at its host when the context map of this
        process's configuration (`configured_host`) names one for `bundled_context_name`, so a
        reference needs no code to forward."""
        self._hosted_at: HostedAt | None = None
        """Where another service hosts this bounded context — the context map, or `hosted_by`."""
        self._runs_here: bool = False
        """Run in this process whatever the map says — it serves the context (`run_here`)."""
        self._circuit: Circuit | bool | None = None
        """What `hosted_by(circuit=...)` said: a circuit of the project's, the address's own one
        (`True`), none (`False`) — or nothing (`None`): the address decides."""
        configured = configured_host(bundled_context_name)
        if configured is not None:
            self._reach_at(configured)

    def _declared_response(self, dto_type: type) -> Any:
        """What the handler of `dto_type` declares it answers — `None` when it declares nothing
        a value can be rebuilt as (`Any`)."""
        handler = self.handler_of(dto_type)
        declared = None if handler is None else get_type_hints(handler.execute).get("return")
        return None if declared is Any else declared

    def _guarding(self, hosted_at: HostedAt) -> Circuit | None:
        """The circuit a call goes through: the one `hosted_by` was handed, the address's own when
        `hosted_by(circuit=True)`, none when `circuit=False` — else what the address says
        (`circuit=on`), and none by default."""
        match self._circuit:
            case Circuit():
                return self._circuit
            case True:
                return circuit_for(replace(hosted_at, circuit=True))
            case False:
                return None
        return circuit_for(hosted_at)

    def _forwarded(
        self, hosted_at: HostedAt, dto: Any, context: Mapping[str, Any]
    ) -> Payload:
        """The call sent where the context is hosted — through its circuit, when it has one: while
        the service keeps not answering, `ContextUnavailable` at once, with the seconds left as
        its `retry_after`, instead of every call waiting out the deadline.

        Context: admitted apart from the call, so only this circuit's refusal becomes
        `ContextUnavailable` — a `CircuitOpen` the hosting service raised is its answer, and
        arrives as itself.
        """
        transport = transport_for(hosted_at)
        circuit = self._guarding(hosted_at)
        if circuit is None:
            return transport.execute(self.name, dto, context)
        try:
            admission = circuit.admit()
        except CircuitOpen as refused:
            unavailable = exceptions.ContextUnavailable(
                f"{self.name} at {hosted_at.address}: {refused}"
            )
            unavailable.retry_after = refused.retry_after
            raise unavailable from refused
        try:
            answer = transport.execute(self.name, dto, context)
        except BaseException as error:
            circuit.settle(admission, error)
            raise
        circuit.settle(admission, None)
        return answer

    def _executed_where_hosted(self, dto: Any, return_type: Any) -> Any:
        """The DTO executed by the service hosting this context, answered as `return_type` — or
        the response its handler here declares, or the class this bus registers under the
        answer's identity; its JSON values when none of them says.

        Context: it is handed the context of the execution calling it — its caller's, then what
        this bus was given — caused by that execution and in its flow (`handed_on`)."""
        hosted_at = self._hosted_at
        if hosted_at is None:
            raise exceptions.SincproFrameworkNotBuilt(
                f"'{self.name}' is not hosted by another service"
            )
        context = handed_on({**self._inherited_context(), **self.current_context()})
        answer = self._forwarded(hosted_at, dto, context)
        response = (
            return_type
            if return_type is not None
            else self._declared_response(type(dto)) or self.dto_registry.get(answer.type)
        )
        try:
            return rebuilt(answer.data, response)
        except ValidationError as error:
            raise exceptions.DTODoesNotFit(
                f"the answer of {self.name} at {hosted_at.address}", error
            ) from None

    @property
    def hosted_at(self) -> HostedAt | None:
        """Where the context map — or `hosted_by` — hosts this bounded context; `None` when
        nothing places it elsewhere."""
        return self._hosted_at

    @property
    def is_reference(self) -> bool:
        """Whether this bus is a reference to a context another service hosts: a client with the
        face of the bus — never built here, no Feature instantiated, no dependency resolved —
        that forwards every call. A context this process runs (`run_here`) never is one."""
        return self._hosted_at is not None and not self._runs_here

    def run_here(self) -> None:
        """Run this context in this process, whatever the context map says — what serving it
        does (`serve`, an open host), so a service whose map names itself never calls itself.
        """
        if not self._runs_here:
            self._runs_here = True
            self._registrations.append(lambda bus: bus.run_here())

    def hosted_by(
        self, address: HostedAt | str, circuit: Circuit | bool | None = None
    ) -> None:
        """Execute every DTO of this context on the service at `address` — what the context map
        says, in code: a URL, or the typed address — and, when `circuit` says, stop calling it
        while it does not answer.

            billing.hosted_by("grpc://10.0.0.5:50051?timeout=5")
            billing.hosted_by(HostedAt(Wire.HTTPS, "billing-service:443", timeout=5))
            billing.hosted_by("grpc://b:50051", circuit=True)                   the address's own
            billing.hosted_by("grpc://b:50051", circuit=Circuit("billing", failures=3))

        `circuit` wins over what the address says (`?circuit=on`): `True` its own circuit,
        `False` none, a `Circuit` that one; left out, the address decides — none by default. An
        address no wire reaches is refused here (`InvalidAddress`), saying what to write.

        See `docs/entrypoints/bounded-contexts-across-services.md`, `docs/core/circuits.md`.
        """
        self._reach_at(HostedAt.of(address))
        self._circuit = circuit
        self._registrations.append(lambda bus: bus.hosted_by(address, circuit))

    @overload
    def serve(self, address: str) -> None: ...

    @overload
    def serve(self, address: str, attach: Literal[Attach.FOREGROUND]) -> None: ...

    @overload
    def serve(
        self,
        address: str,
        attach: Literal[Attach.THREAD, Attach.PROCESS],
    ) -> OpenHost: ...

    def serve(self, address: str, attach: Attach = Attach.FOREGROUND) -> OpenHost | None:
        """Host this bounded context for other services at `address` — `host:port`, `:0` for a
        free port — over gRPC.

            billing.serve("0.0.0.0:50051")                           its own deployment: blocks
            host = billing.serve("0.0.0.0:50051", Attach.THREAD)     beside a REST API
            host = billing.serve("0.0.0.0:50051", Attach.PROCESS)    a subprocess of its own
            host.stop()

        Several at once: `serve_contexts([billing, catalog], address, attach)`.
        """
        hosted = [cast(Any, self)]
        if attach == Attach.FOREGROUND:
            serve_contexts(hosted, address, Attach.FOREGROUND)
            return None
        if attach == Attach.THREAD:
            return serve_contexts(hosted, address, Attach.THREAD)
        return serve_contexts(hosted, address, Attach.PROCESS)
