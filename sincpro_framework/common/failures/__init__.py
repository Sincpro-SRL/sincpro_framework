"""How every wire tells its caller what kind of failure an error is (`FailureKind`, in the core
`exceptions`): the classification, how long to wait before a retry, and the `Problem` every wire
draws — what happened and what to do next."""

from sincpro_framework.common.failures.classification import (
    DEFAULT_RETRY_AFTER,
    PERMANENT,
    RETRYABLE,
    failure_reason,
    is_fault,
    json_safe_validation_errors,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.common.failures.problem import (
    HTTP_STATUS_OF,
    NEXT_STEP_OF,
    TITLE_OF,
    FieldError,
    NextStep,
    Problem,
    pointer,
    problem_of,
)
from sincpro_framework.exceptions import FailureKind

__all__ = [
    "DEFAULT_RETRY_AFTER",
    "FieldError",
    "HTTP_STATUS_OF",
    "NEXT_STEP_OF",
    "NextStep",
    "Problem",
    "TITLE_OF",
    "pointer",
    "problem_of",
    "PERMANENT",
    "RETRYABLE",
    "failure_reason",
    "is_fault",
    "json_safe_validation_errors",
    "refined_failure_kind",
    "retry_after",
    "said_to_the_caller",
]
