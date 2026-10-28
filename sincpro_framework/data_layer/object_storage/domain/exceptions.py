"""What object storage refuses: an object that is not there, one that already is, a publication that
does not open, a URL that cannot be fetched, and a storage that failed."""

from sincpro_framework.context import EntryConflict
from sincpro_framework.data_layer.object_storage.domain.fetch import FetchRefusal
from sincpro_framework.data_layer.object_storage.domain.publication import (
    Publication,
    Refusal,
)
from sincpro_framework.exceptions import (
    ClientError,
    ExternalServiceError,
    FailureKind,
    ProgrammingError,
    ServiceUnavailableError,
)


class ObjectNotFound(ClientError):
    """No object is stored under the key asked."""

    failure_kind = FailureKind.NOT_FOUND


class ObjectAlreadyExists(ClientError):
    """A put `if_absent` found an object under its key — another writer came first."""

    failure_kind = FailureKind.CONFLICT


class PublicationRefused(ClientError):
    """A publication's id that does not open its object, and why (`reason`) — the entrypoint
    answers it as it likes: a 403, a 404, a 410. `publication` is the one refused when it is
    known (`EXPIRED`, `USED_UP`, `WRONG_ACCESS`): its key and metadata, to say what it was —
    "already uploaded: invoice.pdf" — or to go on with its object."""

    failure_kind = FailureKind.PERMISSION_DENIED

    def __init__(
        self, reason: Refusal, message: str, publication: Publication | None = None
    ) -> None:
        """Say why, in the message and as `reason`, and which one when it is known. Every wire
        answers an unknown id as not found, and any other refusal as permission denied."""
        super().__init__(message)
        self.reason = reason
        self.publication = publication
        self.failure_kind = (
            FailureKind.NOT_FOUND
            if reason is Refusal.UNKNOWN
            else FailureKind.PERMISSION_DENIED
        )


class FetchRefused(ClientError):
    """A URL given to fetch that breaks its rules, and why (`reason`) — refused before its bytes
    are read, or as soon as they break one; nothing of it is stored. `status` is what its
    server answered, when it answered."""

    failure_kind = FailureKind.INVALID

    def __init__(
        self, reason: FetchRefusal, url: str, message: str, status: int | None = None
    ) -> None:
        """Say why, in the message and as `reason`, of which `url`."""
        super().__init__(message)
        self.reason = reason
        self.url = url
        self.status = status


class FetchFailed(ServiceUnavailableError):
    """A URL that did not answer in its time, could not be reached, or whose server failed (5xx)
    — nothing of it is stored; asking again later may succeed. `status` is what its server
    answered, when it answered."""

    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        """Say what failed, of which `url`."""
        super().__init__(message)
        self.url = url
        self.status = status
