"""What the pipelines refuse, beside the framework's own errors their code raises."""

from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError


class RunNotFound(ClientError):
    """The run the caller named is not kept — never run, forgotten, or past the store's time."""

    failure_kind = FailureKind.NOT_FOUND
