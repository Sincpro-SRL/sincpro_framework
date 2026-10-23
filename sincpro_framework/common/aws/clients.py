"""`aws_client`: the client an AWS adapter talks through — the project's, or boto3's default."""

from typing import Any

AWS_MISSING = "boto3 is not installed. Install with: pip install sincpro-framework[aws]"


def aws_client(service: str, given: Any = None) -> Any:
    """`given`, or boto3's default client of `service` — refused with the extra to install when
    boto3 is not there."""
    if given is not None:
        return given
    try:
        import boto3
    except ImportError as error:  # pragma: no cover - tests/core/test_core_without_extras.py
        raise ImportError(AWS_MISSING) from error
    return boto3.client(service)
