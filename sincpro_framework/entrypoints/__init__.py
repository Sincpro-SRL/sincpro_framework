"""Entrypoints: everything that starts the application layer from outside — a wire (REST,
JSON-RPC, gRPC, MCP, FastAPI, a FastStream broker, a Lambda fed by SQS), the clock (`cron`) or
workers that run long-lived loops — and the exposure a project declares on its use cases.

Importing this package loads no extra: each gateway imports its library only once it builds or
serves (`FastApiGateway`, which is FastAPI itself, loads it when asked for).

    from sincpro_framework.entrypoints import FastApiGateway, RpcGateway, rest, rpc

    @billing.feature(CommandIssueInvoice)
    @rest.post("/invoices", status=201)
    @rpc()
    class IssueInvoice(Feature): ...

    app = FastApiGateway([billing]).app()    # an ASGI app, returned, never started
"""

from typing import TYPE_CHECKING, Any

from sincpro_framework.entrypoints.domain.bindings import (
    Binding,
    Deprecation,
    GrpcBinding,
    McpBinding,
    QueueBinding,
    RestBinding,
    RpcBinding,
)
from sincpro_framework.entrypoints.domain.packed import PackedFeatureOrAppService
from sincpro_framework.entrypoints.domain.surface import (
    Exposure,
    Group,
    ManifestEntry,
    Operation,
    Profile,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.entrypoint.aws_lambda import sqs_handler
from sincpro_framework.entrypoints.entrypoint.decorators import (
    declare,
    grpc,
    internal,
    mcp,
    queue,
    rest,
    rpc,
)
from sincpro_framework.entrypoints.entrypoint.grpc import GrpcGateway, bus_call
from sincpro_framework.entrypoints.entrypoint.mcp import Entrypoint, McpGateway
from sincpro_framework.entrypoints.entrypoint.queue import QueueGateway, subscribe
from sincpro_framework.entrypoints.entrypoint.rest import RestGateway
from sincpro_framework.entrypoints.entrypoint.rpc import RpcGateway
from sincpro_framework.entrypoints.entrypoint.workers import Loop, Poll, Process
from sincpro_framework.entrypoints.services.catalog import Catalog
from sincpro_framework.entrypoints.services.gateway import Buses, Gateway

if TYPE_CHECKING:
    from sincpro_framework.entrypoints.entrypoint.fastapi import FastApiGateway

__all__ = [
    "Binding",
    "Buses",
    "Catalog",
    "Deprecation",
    "Entrypoint",
    "Exposure",
    "FastApiGateway",
    "Gateway",
    "Group",
    "GrpcBinding",
    "GrpcGateway",
    "Loop",
    "ManifestEntry",
    "McpBinding",
    "McpGateway",
    "Operation",
    "PackedFeatureOrAppService",
    "Poll",
    "Process",
    "Profile",
    "QueueBinding",
    "QueueGateway",
    "Resolved",
    "RestBinding",
    "RestGateway",
    "RpcBinding",
    "RpcGateway",
    "Wire",
    "bus_call",
    "declare",
    "grpc",
    "internal",
    "mcp",
    "queue",
    "rest",
    "rpc",
    "sqs_handler",
    "subscribe",
]


def __getattr__(name: str) -> Any:
    """`FastApiGateway` loads FastAPI — the `[fastapi]` extra — only when asked for."""
    if name == "FastApiGateway":
        from sincpro_framework.entrypoints.entrypoint import fastapi

        return fastapi.FastApiGateway
    raise AttributeError(f"module 'sincpro_framework.entrypoints' has no attribute {name!r}")
