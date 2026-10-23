"""What a use case loaded at runtime raises."""

from sincpro_framework.exceptions import ClientError


class UseCaseRefused(ClientError):
    """A stored use case that cannot be loaded — the bus it would have joined is left as it was."""
