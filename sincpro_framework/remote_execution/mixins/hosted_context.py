"""`HostedContextMixin`: what a `UseFramework` mixes in to stand for a bounded context another
service hosts — a reference that forwards every call — or to host its own for other services.

Context: the context map (`hosted_by` in the settings) is read when the bus is created; a bus a
process serves (`run_here`) runs here whatever the map says.
"""

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

from sincpro_framework.common.serialization import rebuilt
from sincpro_framework.common.transport.addresses import HostedAt
from sincpro_framework.context.infrastructure.tree import handed_on
from sincpro_framework.exceptions import SincproFrameworkNotBuilt
from sincpro_framework.remote_execution.adapters.transport import transport_for
from sincpro_framework.remote_execution.domain.errors import DTODoesNotFit
from sincpro_framework.remote_execution.entrypoint.hosts import (
    Attach,
    OpenHost,
    serve_contexts,
)
from sincpro_framework.remote_execution.infrastructure.configuration import configured_host


class HostedContextMixin:
    """What a `UseFramework` adds to be reached where its context is hosted, or to host it."""

    _logger_name: str
    _registrations: list[Callable[[Any], Any]]
    handler_of: Callable[[type], "type | None"]
    _inherited_context: Callable[[], dict[str, Any]]
    current_context: Callable[[], Any]

    if TYPE_CHECKING:

        @property
        def logger(self) -> LoggerProxy: ...

        @property
        def dto_registry(self) -> Mapping[str, type]: ...

    def _init_hosting(self, bundled_context_name: str) -> None:
        self._hosted_at: HostedAt | None = None
        """Where another service hosts this bounded context — the context map, or `hosted_by`."""
        self._runs_here: bool = False
        """Run in this process whatever the map says — it serves the context (`run_here`)."""
        configured = configured_host(bundled_context_name)
        if configured is not None:
            self._reach_at(configured)

    def _declared_response(self, dto_type: type) -> Any:
        """What the handler of `dto_type` declares it answers — `None` when it declares nothing
        a value can be rebuilt as (`Any`)."""
        handler = self.handler_of(dto_type)
        declared = None if handler is None else get_type_hints(handler.execute).get("return")
        return None if declared is Any else declared

    def _executed_where_hosted(self, dto: Any, return_type: Any) -> Any:
        """The DTO executed by the service hosting this context, answered as `return_type` — or
        the response its handler here declares, or the class this bus registers under the
        answer's identity; its JSON values when none of them says.

        Context: it is handed the context of the execution calling it — its caller's, then what
        this bus was given — caused by that execution and in its flow (`handed_on`)."""
        hosted_at = self._hosted_at
        if hosted_at is None:
            raise SincproFrameworkNotBuilt(
                f"'{self._logger_name}' is not hosted by another service"
            )
        context = handed_on({**self._inherited_context(), **self.current_context()})
        answer = transport_for(hosted_at).execute(self._logger_name, dto, context)
        response = (
            return_type
            if return_type is not None
            else self._declared_response(type(dto)) or self.dto_registry.get(answer.type)
        )
        try:
            return rebuilt(answer.data, response)
        except ValidationError as error:
            raise DTODoesNotFit(
                f"the answer of {self._logger_name} at {hosted_at.address}", error
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

    def _reach_at(self, hosted_at: HostedAt) -> None:
        """Point this bus at the service hosting it — said once in the logs, so a process's start
        shows every context it reaches elsewhere."""
        self._hosted_at = hosted_at
        self.logger.info(
            f"{self._logger_name} is hosted at {hosted_at}: this bus forwards every call"
        )

    def hosted_by(self, address: HostedAt | str) -> None:
        """Execute every DTO of this context on the service at `address` — what the context map
        says, in code: a URL, or the typed address.

            billing.hosted_by("grpc://10.0.0.5:50051?timeout=5")
            billing.hosted_by(HostedAt(Wire.HTTPS, "billing-service:443", timeout=5))

        An address no wire reaches is refused here (`InvalidAddress`), saying what to write.

        See `docs/entrypoints/bounded-contexts-across-services.md`.
        """
        self._reach_at(HostedAt.of(address))
        self._registrations.append(lambda bus: bus.hosted_by(address))

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
