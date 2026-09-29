"""Declared exposure (PRD_14): on which wire a use case is published, and how — the protocol's
vocabulary, technology-free.

    from sincpro_framework.entrypoints.exposure import grpc, mcp, queue, rest, rpc

    @billing.feature(QueryInvoice)
    @auth.requires(BillingPermission.READ)
    @rest.get("/invoices/{invoice_id}")
    @mcp()
    class GetInvoice(Feature): ...

Context: a decorator records a frozen binding in a registry kept by class, never on the class,
and returns it unchanged; the gateway resolves and validates the surface (`entrypoints.gateway`);
each wire translates it. `internal` keeps a use case off every wire, whatever else it declares.
This package imports the application layer only — a `services/` module that declares its
exposure stays free of Starlette, grpc and FastMCP.
"""

from sincpro_framework.entrypoints.exposure.bindings import (
    BUILT_IN_BINDINGS,
    Binding,
    Deprecation,
    GrpcBinding,
    McpBinding,
    QueueBinding,
    RestBinding,
    RpcBinding,
)
from sincpro_framework.entrypoints.exposure.decorators import grpc, mcp, queue, rest, rpc
from sincpro_framework.entrypoints.exposure.internal import internal, is_internal
from sincpro_framework.entrypoints.exposure.registry import (
    Bindings,
    ExposureRefused,
    bindings_of,
    declare,
    registry,
)
from sincpro_framework.entrypoints.exposure.surface import (
    Exposure,
    Group,
    ManifestEntry,
    Operation,
    Resolved,
    Wire,
)

__all__ = [
    "BUILT_IN_BINDINGS",
    "Binding",
    "Bindings",
    "Deprecation",
    "Exposure",
    "ExposureRefused",
    "Group",
    "GrpcBinding",
    "ManifestEntry",
    "McpBinding",
    "Operation",
    "QueueBinding",
    "Resolved",
    "RestBinding",
    "RpcBinding",
    "Wire",
    "bindings_of",
    "declare",
    "grpc",
    "internal",
    "is_internal",
    "mcp",
    "queue",
    "registry",
    "rest",
    "rpc",
]
