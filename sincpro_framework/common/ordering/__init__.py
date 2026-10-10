"""How several things hung on one point run — hooks, interceptors, error handlers, crons: one order for every extension point."""

from sincpro_framework.common.ordering.placement import (
    DEFAULT_SEQUENCE,
    Ordered,
    Placement,
    name_of,
    ordered,
    refused,
)

__all__ = [
    "DEFAULT_SEQUENCE",
    "Ordered",
    "Placement",
    "name_of",
    "ordered",
    "refused",
]
