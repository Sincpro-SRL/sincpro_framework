"""`FastApiGateway`: the REST wire on FastAPI (PRD_15 §2) — one `APIRouter` per bounded context,
one OpenAPI document for generated and hand-written routes together.

    api = FastApiGateway([billing, sales])                   # resource: only what is declared
    api = FastApiGateway([billing], profile="rpc")           # every use case, RPC over HTTP
    api.group(billing, version="v1")                         # /v1/billing/...

    app = api.app()                                          # or, full control:
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    app.include_router(api.router(billing, exclude=[CommandIssueInvoice]))
    assert api.verify(app) == []

Context — two profiles. `resource`, the default, publishes only what a use case declares
(`@rest.post("/invoices", status=201, location="/invoices/{number}")`, PRD_14 DECLARED). `rpc`
publishes every use case (CATALOG) at `POST /{group}/{kebab(Dto)}`, a `Query` on `GET` and on
`POST` for a criteria too long for a URL — documented as RPC over HTTP, never as REST.

Validate once: a Command's body is annotated with its DTO — FastAPI validates it and hands the
instance, path fields merged into the body before that one validation; a `Query` on `GET` (or a
`DELETE`) is read by a dependency from its path and query string, its criteria as JSON, and
validated once. The route calls the bus through `bus_call` — the helper a hand-written route
uses, so the two cannot differ. `Catalog.wrap` is refused: it decorates the dictionary-in call
this wire never makes.
"""

import inspect
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal

from anyio import CapacityLimiter
from fastapi import APIRouter, Body, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from sincpro_framework.auth.entrypoint.access_control import access_control_of
from sincpro_framework.entrypoints.adapters.fastapi import exceptions
from sincpro_framework.entrypoints.adapters.fastapi.calling import (
    bus_call,
    requires_idempotency_key,
)
from sincpro_framework.entrypoints.adapters.fastapi.problems import (
    BODY_EXTENSION,
    INSTALLED,
    SCHEMES_EXTENSION,
    install_problem_handlers,
    problem_response,
)
from sincpro_framework.entrypoints.adapters.rest.openapi import (
    operation_security,
    security_schemes,
)
from sincpro_framework.entrypoints.adapters.rest.routing import (
    is_plain,
    kebab,
    payload_from_query,
)
from sincpro_framework.entrypoints.domain.bindings import RestBinding
from sincpro_framework.entrypoints.domain.layers import Wrapper
from sincpro_framework.entrypoints.domain.surface import (
    Exposure,
    Group,
    Operation,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.entrypoint.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.use_bus import UseFramework

type Profile = Literal["resource", "rpc"]

TEMPLATE_FIELD = re.compile(r"\{([^{}]+)\}")
READ_FROM_URL = ("GET", "DELETE")
NO_CONTENT = 204


def _normalized(path: str) -> str:
    """A path as a router matches it — every `{field}` one placeholder, no trailing slash — so
    two routes that differ only in a field's name are seen as one.

        _normalized("/invoices/{number}/")  ->  "/invoices/{}"
    """
    return TEMPLATE_FIELD.sub("{}", path.rstrip("/") or "/")


def _joined(*parts: str) -> str:
    """Path segments joined by one slash each, the empty ones dropped — always rooted.

        _joined("/v1", "", "/billing/", "invoices")  ->  "/v1/billing/invoices"

    No part at all answers `/`.
    """
    return "/" + "/".join(one.strip("/") for one in parts if one.strip("/"))


def _inlined(schema: Any, defs: Mapping[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """`schema` with each `#/$defs/X` replaced by X itself — a query parameter's schema has no
    document of its own to point into. A model that contains itself stays an open object."""
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            if name in seen or name not in defs:
                return {"type": "object", "title": name}
            return _inlined(defs[name], defs, seen | {name})
        return {key: _inlined(value, defs, seen) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_inlined(one, defs, seen) for one in schema]
    return schema


def _url_parameters(command: type, path_fields: Sequence[str], from_query: bool) -> list[Any]:
    """The path fields — and, when read from the URL, every other field as a query parameter,
    a plain one as itself and any other as JSON."""
    schema = command.model_json_schema() if issubclass(command, BaseModel) else {}
    defs = schema.get("$defs", {})
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    parameters: list[dict[str, Any]] = [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": _inlined(properties.get(name, {"type": "string"}), defs),
        }
        for name in path_fields
    ]
    if not from_query:
        return parameters
    for name, field_schema in properties.items():
        if name in path_fields:
            continue
        described: dict[str, Any] = {
            "name": name,
            "in": "query",
            "required": name in required,
        }
        inlined = _inlined(field_schema, defs)
        if is_plain(field_schema):
            described["schema"] = inlined
        else:
            described["content"] = {"application/json": {"schema": inlined}}
        parameters.append(described)
    return parameters


def _body_without(command: type, path_fields: Sequence[str]) -> dict[str, Any]:
    """The request body's schema: the DTO without the fields the path carries — its nested
    models pointed at the components FastAPI generated for the DTO itself. FastAPI merges
    `openapi_extra` into the schema it wrote, so `problem_document` puts it in place."""
    schema = command.model_json_schema(ref_template="#/components/schemas/{model}")
    schema.pop("$defs", None)
    schema["properties"] = {
        name: value
        for name, value in (schema.get("properties") or {}).items()
        if name not in path_fields
    }
    schema["required"] = [
        one for one in schema.get("required") or () if one not in path_fields
    ]
    if not schema["required"]:
        del schema["required"]
    return {BODY_EXTENSION: schema}


def operation_extra(bus: UseFramework, command: type) -> dict[str, Any]:
    """`openapi_extra` of the operation answering `command` on `bus` — its `security`, what it
    requires (`x-sincpro-requires`), the schemes it names, whether it runs once.

    Context: a hand-written route passes it to `openapi_extra=` so its document says what a
    generated route's says — who may call it and the `Idempotency-Key` it needs.

        operation_extra(billing, CommandIssueInvoice)
        ->  {"security": [...], "x-sincpro-requires": ...,
             "x-sincpro-security-schemes": {...},
             "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True}]}

    1. The operation's security, as the REST document says it (`operation_security`).
    2. When it names any, the bus's security schemes — lifted into `components` later by
       `problem_document`.
    3. Final: a required `Idempotency-Key` header when the use case runs once and its Command
       does not identify itself.
    """
    extra = dict(operation_security(bus, command))
    if extra.get("security"):
        extra[SCHEMES_EXTENSION] = security_schemes([bus])
    handler = bus.handler_of(command)
    if handler is not None and requires_idempotency_key(bus, command):
        extra["parameters"] = [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    return extra


def _kind_of_error(error: type) -> exceptions.FailureKind:
    """The failure kind a declared error class answers with — read from the class, never
    raised, so `problem_responses` can document its status.

    1. Its `failure_kind` class attribute, when it names a known kind.
    2. A stale or duplicate aggregate is a conflict.
    Final: anything else is a domain refusal.
    """
    declared = getattr(error, "failure_kind", None)
    if isinstance(declared, str) and declared in exceptions.FailureKind._value2member_map_:
        return exceptions.FailureKind(declared)
    if issubclass(error, exceptions.StaleAggregate | exceptions.DuplicateAggregate):
        return exceptions.FailureKind.CONFLICT
    return exceptions.FailureKind.DOMAIN


def _declares_once(handler: type) -> bool:
    """Whether `handler` is `@idempotency.once` — imported late, caching is not loaded with
    this module."""
    from sincpro_framework.data_layer.caching import declares_once

    return declares_once(handler)


def problem_responses(
    bus: UseFramework, command: type, errors: Iterable[type] = ()
) -> dict[int | str, dict[str, Any]]:
    """`responses=` of the operation answering `command` on `bus`: every problem it can answer,
    as `application/problem+json` — `errors` adds the domain errors it declares.

    Context: a hand-written route passes it to `responses=` so a client generated from the
    document knows each status it may get, and that every one is a `Problem`.

        problem_responses(billing, CommandIssueInvoice, errors=[InvoiceAlreadyIssued])
        ->  {400: ..., 422: {"description": "...; InvoiceAlreadyIssued", ...}, 500: ...,
             401: ..., 403: ...}

    1. 400, 422 and 500 — any operation can answer them.
    2. 401 and 403 when the bus's `AccessControl` requires anything but `public`.
    3. 409 when the use case runs once — the same request still running.
    4. Final: each declared error under the status of its kind, its name appended to that
       status's description.
    """
    from sincpro_framework.entrypoints.adapters.fastapi.problems import STATUS_OF

    described: dict[int | str, dict[str, Any]] = {
        400: problem_response("The request could not be read"),
        422: problem_response("Validation or the domain refused the request"),
        500: problem_response("Internal error — nothing of the inside is told"),
    }
    access = access_control_of(bus)
    declaration = access.requirements_of(command) if access else None
    if declaration is not None and declaration.kind != "public":
        described[401] = problem_response("Who is calling is not known")
        described[403] = problem_response("The identity may not do this")
    handler = bus.handler_of(command)
    if handler is not None and _declares_once(handler):
        described[409] = problem_response("The same request is still running")
    for error in errors:
        status = STATUS_OF[_kind_of_error(error)]
        held = described.get(status) or problem_response("")
        reason = error.__name__
        held["description"] = "; ".join(one for one in (held["description"], reason) if one)
        described[status] = held
    return described


class _PathIntoBody(APIRoute):
    """A route whose DTO takes fields from the path: they are merged into the JSON body before
    FastAPI validates it, so the DTO validates once, whole. The path wins over the body."""

    def get_route_handler(self) -> Any:
        """FastAPI's handler, behind one that rewrites the request body first."""
        handler = super().get_route_handler()

        async def merged(request: Request) -> Response:
            """The request re-sent with the path fields in its JSON body.

                POST /invoices/F-9/lines  {"amount": 10}  ->  {"amount": 10, "number": "F-9"}

            1. A body that is not JSON, or not an object, goes on untouched — FastAPI answers
               it its own 400 / 422.
            2. The path parameters over the body's fields.
            Final: FastAPI's handler with a request whose `receive` answers the merged body.
            """
            raw = await request.body()
            try:
                body = json.loads(raw) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return await handler(request)
            if not isinstance(body, dict):
                return await handler(request)
            content = json.dumps({**body, **request.path_params}).encode()

            async def receive() -> dict[str, Any]:
                """The merged body, as one ASGI message."""
                return {"type": "http.request", "body": content, "more_body": False}

            return await handler(Request(request.scope, receive))

        return merged


def _read_from_url(command: type) -> Any:
    """A dependency: the DTO out of the path and the query string, validated once."""
    schema = command.model_json_schema() if issubclass(command, BaseModel) else {}

    async def read(request: Request) -> Any:
        """The DTO from the query string and the path — the path wins — or FastAPI's own
        422, each error located under `query`.

            GET /invoices/F-9?status=open  ->  QueryInvoice(number="F-9", status="open")
        """
        payload = {
            **payload_from_query(dict(request.query_params), schema),
            **request.path_params,
        }
        try:
            return command.model_validate(payload)  # type: ignore[attr-defined]
        except ValidationError as refused:
            raise RequestValidationError(
                [
                    {**one, "loc": ("query", *one["loc"])}
                    for one in refused.errors(include_url=False)
                ]
            ) from refused

    return read


class FastApiWire(Wire[RestBinding]):
    """The REST wire's port on FastAPI: what it derives, what it refuses, what it builds."""

    binding = RestBinding

    def __init__(self, profile: Profile, limiter: CapacityLimiter | None = None) -> None:
        """`profile` decides how a path is derived and whether a `Query` also answers `POST`;
        `limiter` bounds the worker threads every route's bus call runs on."""
        self.profile: Profile = profile
        self.limiter = limiter
        self._repeated: set[str] = set()

    # Deriving and naming

    def derive(self, operation: Operation, group: Group) -> RestBinding:
        """`GET` for a `Query`, `POST` for any other Command; `/{kebab(Dto)}` in the rpc profile,
        the name without `Command`/`Query` in the resource one; 204 for what answers nothing.

            derive(<CommandIssueInvoice>, Group(alias="billing"))   # profile "resource"
            ->  RestBinding(method="POST", path="/issue-invoice")
            derive(<QueryInvoices>, Group(alias="billing"))         # profile "rpc"
            ->  RestBinding(method="GET", path="/query-invoices")
        """
        name = operation.command.__name__
        if self.profile == "resource":
            name = name.removeprefix("Command").removeprefix("Query") or name
        fields: dict[str, Any] = {
            "method": "GET" if operation.is_query else "POST",
            "path": f"/{kebab(name)}",
        }
        if operation.response is type(None):
            fields["status"] = NO_CONTENT
        if group.tags:
            fields["tags"] = group.tags
        return RestBinding(**fields)

    def prefix_of(self, group: Group) -> str:
        """`/{version}{prefix}` — the prefix is `/{alias}` unless the group says one."""
        prefix = group.prefix if group.prefix is not None else f"/{group.alias}"
        return _joined(group.version or "", prefix) if (group.version or prefix) else ""

    def name_of(self, resolved: Resolved[RestBinding]) -> str:
        """The `operationId`: the DTO's name, qualified by the group when two share it."""
        name = resolved.operation.command.__name__
        return f"{resolved.operation.alias}_{name}" if name in self._repeated else name

    def _methods(self, resolved: Resolved[RestBinding]) -> list[str]:
        """A `Query` on its derived rpc path answers `POST` too, for a criteria too long for a
        URL — a path the use case declared answers only what it declared."""
        method = resolved.binding.method or "POST"
        derived = f"/{kebab(resolved.operation.command.__name__)}"
        if (
            self.profile == "rpc"
            and resolved.operation.is_query
            and method == "GET"
            and resolved.binding.path == derived
        ):
            return ["GET", "POST"]
        return [method]

    def full_path(self, resolved: Resolved[RestBinding]) -> str:
        """The path an operation is served at — its group's prefix and its own path.

            full_path(<"/invoices" in Group(alias="billing", version="v1")>)
            ->  "/v1/billing/invoices"

        The prefix is the group's version and prefix (`prefix_of`).
        """
        return _joined(self.prefix_of(resolved.group), resolved.binding.path or "")

    # Validating

    def validate(self, surface: Sequence[Resolved[RestBinding]]) -> list[str]:
        """Every reason the surface cannot be served over REST — empty when it can.

            validate([GET /v1/billing/invoices/{id}, GET /v1/billing/invoices/{number}])
            ->  ["rest: GET /v1/billing/invoices/{number} answers both QueryInvoice and
                 QueryInvoiceByNumber"]
            validate([<binding with body="merge-patch">])
            ->  ["CommandPatchInvoice (rest): body='merge-patch' is served in phase 2 ..."]

        Context: the Command names that appear more than once are kept first, so their
        `operationId`s are qualified by their group.

        1. Two operations on one method and path — `{id}` and `{number}` are one path.
        2. Two operations with one `operationId`, once qualified by their group.
        3. Final: what is declared but served only in phase 2 — merge patch, `If-Match`.
        """
        names = [one.operation.command.__name__ for one in surface]
        self._repeated = {name for name in names if names.count(name) > 1}
        problems: list[str] = []
        taken: dict[tuple[str, str], str] = {}
        ids: dict[str, str] = {}
        for one in surface:
            command = one.operation.command.__name__
            path = self.full_path(one)
            for method in self._methods(one):
                key = (method, _normalized(path))
                if key in taken:
                    problems.append(
                        f"rest: {method} {path} answers both {taken[key]} and {command}"
                    )
                taken[key] = command
            operation_id = self.name_of(one)
            if operation_id in ids:
                problems.append(
                    f"rest: operationId {operation_id} names both {ids[operation_id]} and "
                    f"{one.operation.command.__module__}.{command}"
                )
            ids[operation_id] = f"{one.operation.command.__module__}.{command}"
            if one.binding.body == "merge-patch":
                problems.append(
                    f"{command} (rest): body='merge-patch' is served in phase 2 — declare its "
                    "fields optional and PATCH with JSON"
                )
            if one.binding.concurrency == "if-match":
                problems.append(
                    f"{command} (rest): concurrency='if-match' is served in phase 2 — an "
                    "If-Match accepted and not checked would be a lie"
                )
        return problems

    # Building

    def add_route(self, router: APIRouter, resolved: Resolved[RestBinding]) -> None:
        """Add one operation to `router` — one FastAPI route per method it answers.

        Context: the route's OpenAPI is written here, not by a template: the problems it can
        answer, its security, its path and query parameters inlined, a body without the path's
        fields — so the generated document says what the route does.

            add_route(router, <CommandIssueInvoice, POST "/invoices", answers a response>)
            ->  None; POST /invoices on `router`, status 200, operationId from `name_of`
            add_route(router, <CommandVoidInvoice, DELETE "/invoices/{id}", answers nothing>)
            ->  None; DELETE /invoices/{id}, `id` a path parameter, status 204

        1. The path (derived when the binding has none), its `{fields}`, the status — 204 when
           the use case answers nothing — and the `operationId`.
        2. What every method shares: response model, problem responses, tags, summary (the
           description's first line), deprecation.
        3. Per method:
           3.1 `GET` / `DELETE` read the DTO from the URL, every field a query parameter; any
               other reads the body, without the fields the path carries.
           3.2 `x-sincpro-dto` and `x-sincpro-layer` name what answers it.
           3.3 The `POST` twin of an rpc `Query` gets `_by_body` on its `operationId`.
        Final: `add_api_route`, with `_PathIntoBody` when a body route takes fields from the
        path.
        """
        operation, binding = resolved.operation, resolved.binding
        command = operation.command
        path = binding.path or f"/{kebab(command.__name__)}"
        path_fields = TEMPLATE_FIELD.findall(path)
        status = binding.status or (NO_CONTENT if operation.response is type(None) else 200)
        operation_id = self.name_of(resolved)
        description = operation.description or ""
        common: dict[str, Any] = {
            "status_code": status,
            "response_model": (
                None if operation.response in (None, type(None)) else operation.response
            ),
            "responses": problem_responses(operation.bus, command, binding.responses),
            "tags": list(binding.tags or resolved.group.tags or (operation.alias,)),
            "summary": binding.summary or description.split("\n", 1)[0] or None,
            "description": description or None,
            "deprecated": binding.deprecated is not None or None,
        }
        if status == NO_CONTENT:
            common["response_class"] = Response
        call = bus_call(operation.bus, self.limiter)
        for method in self._methods(resolved):
            from_url = method in READ_FROM_URL
            extra = operation_extra(operation.bus, command)
            extra["parameters"] = [
                *_url_parameters(command, path_fields, from_url),
                *extra.get("parameters", []),
            ]
            if not from_url and path_fields:
                extra.update(_body_without(command, path_fields))
            extra["x-sincpro-dto"] = command.__name__
            extra["x-sincpro-layer"] = str(operation.layer)
            by_body = method == "POST" and len(self._methods(resolved)) > 1
            router.add_api_route(
                path,
                _endpoint(
                    command,
                    from_url,
                    call,
                    status,
                    binding.location,
                    self.prefix_of(resolved.group),
                ),
                methods=[method],
                operation_id=f"{operation_id}_by_body" if by_body else operation_id,
                openapi_extra=extra,
                route_class_override=(
                    _PathIntoBody if path_fields and not from_url else APIRoute
                ),
                **common,
            )

    def build(self, surface: Sequence[Resolved[RestBinding]]) -> dict[str, APIRouter]:
        """One `APIRouter` per bounded context, its group's prefix on it.

            build([<billing: IssueInvoice, Group(version="v1")>, <billing: QueryInvoice>,
                   <stock: Reserve>])
            ->  {"billing": APIRouter(prefix="/v1/billing"), "stock": APIRouter(...)}

        The router's prefix is the one of the first operation of that bounded context.
        """
        routers: dict[str, APIRouter] = {}
        for one in surface:
            alias = one.operation.alias
            router = routers.get(alias)
            if router is None:
                router = routers[alias] = APIRouter(prefix=self.prefix_of(one.group))
            self.add_route(router, one)
        return routers


def _location(request: Request, template: str, answer: Any, prefix: str) -> str:
    """`template` filled from the answer's fields, under the group's prefix and any prefix the
    app included the router under — what the request path has before this route's own."""
    route = request.scope.get("route")
    own = getattr(route, "path_format", "").format(**request.path_params)
    path = request.url.path
    outer = path[: len(path) - len(own)] if own and path.endswith(own) else ""
    values = answer.model_dump() if isinstance(answer, BaseModel) else dict(answer or {})
    return _joined(outer, prefix, template.format(**values))


def _endpoint(
    command: type,
    from_url: bool,
    call: Any,
    status: int,
    location: str | None,
    prefix: str,
) -> Any:
    """The route's function — its signature built for FastAPI: the DTO from the body (or from
    the URL), the bus call as a dependency."""

    async def endpoint(request: Request, response: Response, dto: Any, call: Any) -> Any:
        """Run the DTO — an empty one when an all-optional body was not sent — and answer.

        1. `call(dto)`: the bus, through `bus_call`.
        2. A 204 answers no body, whatever the bus returned.
        Final: the answer, with a `Location` header filled from it when the binding declares
        one.
        """
        answer = await call(dto if dto is not None else command())
        if status == NO_CONTENT:
            return Response(status_code=NO_CONTENT)
        if location is not None:
            response.headers["Location"] = _location(request, location, answer, prefix)
        return answer

    if from_url:
        dto = inspect.Parameter(
            "dto",
            inspect.Parameter.KEYWORD_ONLY,
            default=Depends(_read_from_url(command)),
        )
    else:
        required = bool(
            issubclass(command, BaseModel)
            and any(info.is_required() for info in command.model_fields.values())
        )
        dto = inspect.Parameter(
            "dto",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[command, Body()],
            **({} if required else {"default": None}),
        )
    endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request),
            inspect.Parameter(
                "response", inspect.Parameter.KEYWORD_ONLY, annotation=Response
            ),
            dto,
            inspect.Parameter("call", inspect.Parameter.KEYWORD_ONLY, default=Depends(call)),
        ]
    )
    return endpoint


def _served(app: FastAPI) -> Iterable[tuple[str, APIRoute]]:
    """Every API route `app` serves, with the path it is served at — through FastAPI's route
    contexts where it has them (included routers keep their own routes), else its route list.
    """
    try:
        from fastapi.routing import (
            iter_route_contexts,  # pyright: ignore[reportAttributeAccessIssue]
        )
    except ImportError:
        for route in app.routes:
            if isinstance(route, APIRoute):
                yield route.path, route
        return
    for context in iter_route_contexts(app.routes):
        original = context.original_route
        if isinstance(original, APIRoute):
            yield context.path or original.path, original


def _scrape_app() -> Any | None:
    """The ASGI app answering a Prometheus scrape — when the process records to one."""
    from sincpro_framework.observability.metrics import metrics

    recorder = metrics.recorder
    asgi_app = getattr(recorder, "asgi_app", None)
    return asgi_app() if callable(asgi_app) else None


class FastApiGateway(Gateway):
    """The REST wire on FastAPI — the buses' surface as `APIRouter`s, or as a whole app.

    Context: a project that serves HTTP with FastAPI publishes its use cases through this,
    and adds routes of its own beside them through `bus_call`; `verify(app)` checks the two
    together. What it builds is returned, never started.

        api = FastApiGateway([billing]).group(billing, version="v1")
        app = api.app()                 # /v1/billing/..., /healthz, problems installed
        uvicorn.run(app)
    """

    wire = "rest"

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
        profile: Profile = "resource",
        exposure: Exposure | None = None,
        unguarded: bool = False,
        worker_threads: int | None = None,
    ) -> None:
        """`profile="rpc"` publishes the catalog unless `exposure` says otherwise;
        `worker_threads` bounds the threads the bus runs on (anyio's default, 40)."""
        self._wire = FastApiWire(
            profile, CapacityLimiter(worker_threads) if worker_threads else None
        )
        mode = exposure or (Exposure.CATALOG if profile == "rpc" else Exposure.DECLARED)
        super().__init__(
            instances,
            layers,
            title,
            version,
            exposure=mode,
            unguarded=unguarded,
            port=self._wire,
        )

    @property
    def profile(self) -> Profile:
        """`"resource"` — only what is declared — or `"rpc"` — every use case, RPC over
        HTTP."""
        return self._wire.profile

    def add(
        self,
        alias: str | UseFramework,
        framework_instance: UseFramework | None = None,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> "FastApiGateway":
        """`Gateway.add`, with `wrap` refused: a route validates the DTO once and calls the
        bus, never the dictionary-in run a wrapper decorates — an interceptor does that job.
        """
        if wrap:
            raise exceptions.ProgrammingError(
                "FastApiGateway: wrap decorates the dictionary-in call this wire never makes — "
                "the route validates the DTO once and calls the bus; use an interceptor"
            )
        super().add(alias, framework_instance, include, exclude)
        return self

    def _surface_of(
        self, bus: UseFramework | str | None, exclude: Iterable[type]
    ) -> list[Resolved[RestBinding]]:
        """The validated surface narrowed to one context — the bus or its alias, every one
        when `None` — without the Commands in `exclude`."""
        left_out = set(exclude)
        return [
            one
            for one in self.surface()
            if one.operation.command not in left_out
            and (bus is None or one.operation.alias == bus or one.operation.bus is bus)
        ]

    def routers(self) -> dict[str, APIRouter]:
        """One `APIRouter` per bounded context, by alias — each with its group's prefix."""
        return self.build()

    def router(
        self, bus: UseFramework | str | None = None, exclude: Iterable[type] = ()
    ) -> APIRouter:
        """The routes of one context — the bus or its alias — or of every one, with their
        prefixes; `exclude` leaves out the Commands a project answers with routes of its own.

            gateway.router("billing")         ->  APIRouter(prefix="/v1/billing")
            gateway.router()                  ->  one APIRouter including every context's
            gateway.router(billing, exclude=[CommandIssueInvoice])
            ->  billing's routes but POST /invoices, answered by a route of the project's
            gateway.router("shipping")        ->  ProgrammingError: no bus of this gateway

        A bus that has nothing exposed answers an empty router, not an error.
        """
        surface = self._surface_of(bus, exclude)
        if (
            bus is not None
            and not surface
            and not any(
                alias == bus or catalog.framework_instance is bus
                for alias, catalog in self.catalogs.items()
            )
        ):
            raise exceptions.ProgrammingError(
                f"router({bus!r}): no bus of this gateway is {bus!r}"
            )
        built = self._wire.build(surface)
        if bus is not None and len(built) == 1:
            return next(iter(built.values()))
        joined = APIRouter()
        for one in built.values():
            joined.include_router(one)
        return joined

    def operation_extra(self, bus: UseFramework, command: type) -> dict[str, Any]:
        """The module's `operation_extra`, reachable from the gateway a project holds."""
        return operation_extra(bus, command)

    def problem_responses(
        self, bus: UseFramework, command: type
    ) -> dict[int | str, dict[str, Any]]:
        """The module's `problem_responses`, with no declared errors."""
        return problem_responses(bus, command)

    def app(
        self,
        health_path: str | None = "/healthz",
        metrics_path: str | None = "/metrics",
        **fastapi_kwargs: Any,
    ) -> FastAPI:
        """Every context's router in one FastAPI app, its problem handlers installed, a health
        check, and — when the process's metrics are scraped (`PrometheusRecorder`) — the scrape
        at `metrics_path`. `fastapi_kwargs` go to `FastAPI(...)`.

            gateway.app()                     ->  FastAPI with /healthz and every route
            gateway.app(health_path=None, docs_url=None)
            ->  no health check, no Swagger UI
            GET /healthz                      ->  200 {"status": "ok"}  (503 "unhealthy")

        1. `separate_input_output_schemas` off unless said — one schema per DTO.
        2. The app, titled and versioned as the gateway; the problem handlers installed.
        3. Every context's routes (`router()`).
        4. The health check at `health_path`, out of the OpenAPI document — unless `None`.
        Final: the scrape mounted at `metrics_path`, when the process's metrics are scraped.
        """
        fastapi_kwargs.setdefault("separate_input_output_schemas", False)
        app = FastAPI(title=self._title, version=self._version, **fastapi_kwargs)
        install_problem_handlers(app)
        app.include_router(self.router())
        if health_path is not None:
            gateway = self

            @app.get(health_path, include_in_schema=False)
            async def health() -> Response:
                """200 `{"status": "ok"}` while every bus is built, else 503 `unhealthy`."""
                healthy = gateway.is_healthy()
                return Response(
                    json.dumps({"status": "ok" if healthy else "unhealthy"}),
                    status_code=200 if healthy else 503,
                    media_type="application/json",
                )

        scrape = _scrape_app()
        if metrics_path is not None and scrape is not None:
            app.mount(metrics_path, scrape)
        return app

    def verify(self, app: FastAPI | None = None) -> list[str]:
        """Every reason the surface would be refused — and, given `app`, what its routes break
        together, generated and hand-written: one method and path twice, one `operationId`
        twice, problem handlers never installed.

            gateway.verify()                  ->  []  (the surface alone)
            gateway.verify(gateway.app())     ->  []
            gateway.verify(<app with a route of its own on POST /v1/billing/invoices>)
            ->  ["rest: POST /v1/billing/invoices answered twice — ... and issue_by_hand"]

        1. What `Gateway.verify` finds in the surface; no `app`, that is all.
        2. Each route the app serves, by its full path:
           2.1 each method and normalized path seen once — a second one named with both;
           2.2 each `operationId` seen once — a second one named with both paths.
        3. The problem handlers not installed on the app.
        Final: the problems, each once, in the order found.
        """
        problems = super().verify()
        if app is None:
            return problems
        taken: dict[tuple[str, str], str] = {}
        ids: dict[str, str] = {}
        for path, route in _served(app):
            name = getattr(route.endpoint, "__qualname__", route.name)
            for method in sorted(route.methods or ()):
                key = (method, _normalized(path))
                if key in taken:
                    problems.append(
                        f"rest: {method} {path} answered twice — {taken[key]} and {name}"
                    )
                taken[key] = name
            if route.operation_id:
                if route.operation_id in ids:
                    problems.append(
                        f"rest: operationId {route.operation_id} on {ids[route.operation_id]} "
                        f"and {path}"
                    )
                ids[route.operation_id] = path
        if not getattr(app.state, INSTALLED, False):
            problems.append(
                "rest: install_problem_handlers(app) was not called — failures would answer "
                "FastAPI's shapes, a domain refusal a 500"
            )
        return list(dict.fromkeys(problems))
