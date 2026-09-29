"""Driving adapters: the buses' use cases on a wire. Shared: `catalog` (one bus as a filtered list
of use cases), `gateway` (N buses, automatic or narrowed — what every wire extends), `exposure`
(`internal`, on no wire). Wires: `rpc` (JSON-RPC 2.0), `grpc`, `mcp`, `rest` (OpenAPI 3.1)."""

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.exposure import internal, is_internal
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway

__all__ = [
    "Buses",
    "Catalog",
    "DEFAULT_LAYERS",
    "Gateway",
    "PackedFeatureOrAppService",
    "internal",
    "is_internal",
]
