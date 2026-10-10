"""How every wire tells its caller what kind of failure an error is (`FailureKind`, in the core
`exceptions`): the classification, and how long to wait before a retry."""

from sincpro_framework.common.failures.classification import (
    DEFAULT_RETRY_AFTER,
    PERMANENT,
    RETRYABLE,
    failure_reason,
    json_safe_validation_errors,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.exceptions import FailureKind

__all__ = [
    "DEFAULT_RETRY_AFTER",
    "PERMANENT",
    "RETRYABLE",
    "failure_reason",
    "json_safe_validation_errors",
    "refined_failure_kind",
    "retry_after",
    "said_to_the_caller",
]
