"""What kind of failure an error is (`kind`), and how every wire tells its caller (`classification`)."""

from sincpro_framework.common.failures.classification import (
    DEFAULT_RETRY_AFTER,
    PERMANENT,
    RETRYABLE,
    failure_kind,
    failure_reason,
    json_safe_validation_errors,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.common.failures.kind import FailureKind

__all__ = [
    "DEFAULT_RETRY_AFTER",
    "FailureKind",
    "PERMANENT",
    "RETRYABLE",
    "failure_kind",
    "failure_reason",
    "json_safe_validation_errors",
    "refined_failure_kind",
    "retry_after",
    "said_to_the_caller",
]
