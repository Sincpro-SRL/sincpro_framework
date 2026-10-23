"""What a key-value store raises."""

from sincpro_framework.exceptions import ServiceUnavailableError


class StoreUnavailable(ServiceUnavailableError):
    """The store did not answer — down, unreachable, past its timeout. Whoever holds context,
    drafts or cached values there retries later; the cache answers from the process meanwhile.
    """
