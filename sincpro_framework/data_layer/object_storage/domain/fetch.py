"""Why a URL given to fetch is refused before, or while, its bytes reach the store."""

from enum import StrEnum


class FetchRefusal(StrEnum):
    """What a URL broke of the rules its fetch was given."""

    SCHEME_NOT_ALLOWED = "scheme_not_allowed"
    """Not one of the schemes the fetch was told to take (`schemes=`)."""
    HOST_NOT_ALLOWED = "host_not_allowed"
    """Not one of the hosts the fetch was told to take — the URL's, or a redirect's."""
    REDIRECTED = "redirected"
    """It answered a redirect, and the fetch does not follow them."""
    TOO_LARGE = "too_large"
    """More bytes than `max_bytes` — said by its `Content-Length`, or counted as they came."""
    ANSWERED = "answered"
    """Its server answered a client error (4xx): the URL is wrong, or not this caller's."""
