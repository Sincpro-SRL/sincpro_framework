"""`Session`: what a login is, as the store holds it and a caller reads it back."""

from datetime import datetime
from typing import Any

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Session(DataTransferObject):
    """One session: the id a client holds, what the backend keeps for it, and until when it lasts
    if nobody uses it again.

        Session(id="9fK…", values={"user_id": "ana", "token": "…"},
                opened_at=2026-10-26 12:00, expires_at=2026-11-25 12:00)
    """

    id: str
    """What the client holds — a cookie, a bearer — and sends back: random, never guessed."""
    values: dict[str, Any]
    """What the backend keeps for the session: who logged in, the token of the system behind."""
    opened_at: datetime
    """When it was opened — what `at_most` is counted from."""
    expires_at: datetime | None
    """When it lapses if nobody uses it again; `None` never — each use moves it."""
