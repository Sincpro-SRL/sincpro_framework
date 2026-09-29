"""Kept where PRD_12 documented it: the classification of failures lives in `transport.failures`,
the core the entrypoints and remote execution share."""

from sincpro_framework.transport.failures import (
    FailureKind,
    failure_kind,
    json_safe_validation_errors,
    said_to_the_caller,
)

__all__ = ["FailureKind", "failure_kind", "json_safe_validation_errors", "said_to_the_caller"]
