"""What a migration raises."""

from sincpro_framework.exceptions import ExternalServiceError, ProgrammingError


class MigrationRefused(ProgrammingError):
    """Nothing ran: the system is not where the command can start from, or it asked for
    something the code does not have."""


class MigrationFailed(ExternalServiceError):
    """A step failed; the steps before it stay applied, the ones after it did not run."""
