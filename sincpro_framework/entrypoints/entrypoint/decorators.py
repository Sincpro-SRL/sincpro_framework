"""The decorators a use case declares its exposure with — one per protocol, each recording one
binding in the registry and returning the class unchanged, so their order never matters.

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE)
    @rest.post("/invoices", status=201, location="/invoices/{number}")
    @rpc()
    @grpc()
    @mcp(title="Emitir factura", destructive=False)
    class IssueInvoice(Feature): ...

Context: a binding is per protocol, a host is per technology — changing the REST host changes
no use case. Nothing here imports a transport library.
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework.entrypoints.domain.bindings import (
    Binding,
    Deprecation,
    GrpcBinding,
    HttpMethod,
    McpBinding,
    QueueBinding,
    RestBinding,
    RestBody,
    RestConcurrency,
    RpcBinding,
)
from sincpro_framework.entrypoints.infrastructure.registry import registry

type Deprecated = Deprecation | bool | None
"""`True` for a deprecation with no dates; a `Deprecation` to say since, sunset, replacement."""


def declare[T: type](cls: T, binding: Binding) -> T:
    """Record `binding` for the handler `cls` — what every exposure decorator does, and what a
    project's own transport's decorator does with its own binding type."""
    return registry.declare(cls, binding)


def _deprecation(deprecated: Deprecated) -> Deprecation | None:
    """What a decorator's `deprecated=` says, as the binding holds it — `True` a deprecation
    with no dates, a `Deprecation` as given, nothing else none.

        _deprecation(True)                                  ->  Deprecation()
        _deprecation(Deprecation(sunset=date(2027, 1, 1)))  ->  the same Deprecation
        _deprecation(None) / _deprecation(False)            ->  None
    """
    if deprecated is True:
        return Deprecation()
    return deprecated or None


def _declared_only[B: Binding](binding_type: type[B], **fields: Any) -> B:
    """The binding with only what was said: a field left at its default stays unset, so the
    composition's merge (PRD_14 §7) sees what the decorator declared, not its defaults."""
    defaults = binding_type.model_fields
    return binding_type(
        **{
            name: value
            for name, value in fields.items()
            if name not in defaults or value != defaults[name].default
        }
    )


def _declaring[T: type](binding: Binding) -> Callable[[T], T]:
    """The class decorator that records `binding` for the class it decorates."""

    def declared(cls: T) -> T:
        """Record the binding for `cls` and hand `cls` back unchanged."""
        return declare(cls, binding)

    return declared


class _Rest:
    """`rest.get/post/put/patch/delete(path, ...)` fix the method; `rest(path, ...)` leaves it
    to be derived — `GET` for a `Query`, `POST` for any other Command."""

    def __call__[T: type](
        self,
        path: str | None = None,
        method: HttpMethod | None = None,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """An HTTP operation, its method left to be derived unless given.

            @rest("/invoices/{number}")              # GET for a Query, POST for a Command
            @rest(method="PUT")                      # the path derived from the DTO's name

        `status` the success code, `location` the `Location` template of a 201, `responses`
        the domain errors to document, `body="merge-patch"` a partial body, `concurrency=
        "if-match"` an `If-Match` precondition — a REST host refuses at build time what it
        does not serve yet. A field not given is derived by the REST wire.
        """
        binding = _declared_only(
            RestBinding,
            method=method,
            path=path,
            status=status,
            location=location,
            tags=tags,
            summary=summary,
            responses=responses,
            body=body,
            concurrency=concurrency,
            deprecated=_deprecation(deprecated),
        )

        return _declaring(binding)

    def _verb[T: type](
        self,
        method: HttpMethod,
        path: str,
        status: int | None,
        location: str | None,
        tags: tuple[str, ...],
        summary: str | None,
        responses: tuple[type, ...],
        body: RestBody | None,
        concurrency: RestConcurrency | None,
        deprecated: Deprecated,
    ) -> Callable[[T], T]:
        """The decorator with `method` fixed — what each verb below calls."""
        return self(
            path,
            method=method,
            status=status,
            location=location,
            tags=tags,
            summary=summary,
            responses=responses,
            body=body,
            concurrency=concurrency,
            deprecated=deprecated,
        )

    def get[T: type](
        self,
        path: str,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """A `GET` at `path` — a read: the Command must be a `Query`.

            @rest.get("/invoices/{number}")

        Every other field as `rest(...)` takes it.
        """
        return self._verb(
            "GET",
            path,
            status,
            location,
            tags,
            summary,
            responses,
            body,
            concurrency,
            deprecated,
        )

    def post[T: type](
        self,
        path: str,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """A `POST` at `path` — create or act.

            @rest.post("/invoices", status=201, location="/invoices/{number}")

        Every other field as `rest(...)` takes it.
        """
        return self._verb(
            "POST",
            path,
            status,
            location,
            tags,
            summary,
            responses,
            body,
            concurrency,
            deprecated,
        )

    def put[T: type](
        self,
        path: str,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """A `PUT` at `path` — replace the resource whole.

            @rest.put("/invoices/{number}")

        Every other field as `rest(...)` takes it.
        """
        return self._verb(
            "PUT",
            path,
            status,
            location,
            tags,
            summary,
            responses,
            body,
            concurrency,
            deprecated,
        )

    def patch[T: type](
        self,
        path: str,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """A `PATCH` at `path` — change part of a resource; with no `body="merge-patch"`
        the Command may require no field outside the path.

            @rest.patch("/invoices/{number}", body="merge-patch")
        """
        return self._verb(
            "PATCH",
            path,
            status,
            location,
            tags,
            summary,
            responses,
            body,
            concurrency,
            deprecated,
        )

    def delete[T: type](
        self,
        path: str,
        status: int | None = None,
        location: str | None = None,
        tags: tuple[str, ...] = (),
        summary: str | None = None,
        responses: tuple[type, ...] = (),
        body: RestBody | None = None,
        concurrency: RestConcurrency | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """A `DELETE` at `path` — it answers no body: `execute` answers `None`.

            @rest.delete("/invoices/{number}", status=204)

        Every other field as `rest(...)` takes it.
        """
        return self._verb(
            "DELETE",
            path,
            status,
            location,
            tags,
            summary,
            responses,
            body,
            concurrency,
            deprecated,
        )


rest = _Rest()
"""The REST decorator — `@rest.post("/invoices")`, or `@rest()` to derive it all."""


def rpc[T: type](
    name: str | None = None, notification: bool = False, deprecated: Deprecated = None
) -> Callable[[T], T]:
    """A JSON-RPC method — `name` derived (`billing.issue_invoice`) when not given.

        @rpc()                                    # billing.issue_invoice
        @rpc("billing.invoices.issue")
        @rpc(notification=True)                   # answers nothing to the caller

    A name starting with `rpc.` is refused — the specification reserves it.
    """
    binding = _declared_only(
        RpcBinding, name=name, notification=notification, deprecated=_deprecation(deprecated)
    )

    return _declaring(binding)


def grpc[T: type](
    service: str | None = None, method: str | None = None, deprecated: Deprecated = None
) -> Callable[[T], T]:
    """A gRPC method — the context's `{Alias}Service` and the DTO's name without its kind,
    unless given.

        @grpc()                                   # BillingService / IssueInvoice
        @grpc(service="InvoicesService", method="Issue")

    `service` splits one context's single service in several.
    """
    binding = _declared_only(
        GrpcBinding, service=service, method=method, deprecated=_deprecation(deprecated)
    )

    return _declaring(binding)


def mcp[T: type](
    name: str | None = None,
    title: str | None = None,
    read_only: bool | None = None,
    destructive: bool | None = None,
    open_world: bool = False,
    deprecated: Deprecated = None,
) -> Callable[[T], T]:
    """An MCP tool — its name derived from the DTO, `read_only` from the Command's kind.

        @mcp(title="Emitir factura", destructive=False)
        @mcp(open_world=True)                     # it reaches outside the system

    `read_only` may not contradict the Command's kind (a `Query` reads). `destructive` is the
    hint the framework cannot prove: undeclared, a tool that writes stays destructive. Hints
    are never authorization.
    """
    binding = _declared_only(
        McpBinding,
        name=name,
        title=title,
        read_only=read_only,
        destructive=destructive,
        open_world=open_world,
        deprecated=_deprecation(deprecated),
    )

    return _declaring(binding)


class _Queue:
    """`queue.consumes(channel, ...)` runs a Command from one channel, point to point;
    `queue.hears(group=...)` runs a DomainEvent in a consumer group (PRD_15 §5.1)."""

    def consumes[T: type](
        self,
        channel: str,
        producers: tuple[str, ...] = (),
        max_attempts: int | None = None,
        concurrency: int | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """Run this Command for each message on `channel` — point to point, one handler per
        channel.

            @queue.consumes("billing.invoices.issue", producers=("urn:svc:sales",))

        `producers` the identities allowed to send it, `max_attempts` the deliveries before
        the message is dead-lettered, `concurrency` how many workers run it at once.
        """
        binding = _declared_only(
            QueueBinding,
            kind="consumes",
            channel=channel,
            producers=producers,
            max_attempts=max_attempts,
            concurrency=concurrency,
            deprecated=_deprecation(deprecated),
        )

        return _declaring(binding)

    def hears[T: type](
        self,
        group: str | None = None,
        channel: str | None = None,
        max_attempts: int | None = None,
        concurrency: int | None = None,
        deprecated: Deprecated = None,
    ) -> Callable[[T], T]:
        """Run this handler for each DomainEvent of its type, in a consumer `group` — every
        group hears every event once.

            @queue.hears(group="billing-ledger")

        `group` defaults to the context's namespace or alias; `channel` to the event's wire
        name.
        """
        binding = _declared_only(
            QueueBinding,
            kind="hears",
            group=group,
            channel=channel,
            max_attempts=max_attempts,
            concurrency=concurrency,
            deprecated=_deprecation(deprecated),
        )

        return _declaring(binding)


queue = _Queue()
"""The queue decorator — `@queue.consumes(channel)` for a Command, `@queue.hears()` for an
event."""
