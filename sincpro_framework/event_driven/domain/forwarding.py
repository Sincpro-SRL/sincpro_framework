"""What a forwarding reports: how one message went (`Forwarded`), and what a project hangs on it to
hear every delivery and every failed attempt (`ForwardingHook`).

    future = Publisher(queue, forwarding=True).publish(event)
    future.result()      ->  Forwarded(attempts=2, delivered_at=…, last_error=None, set_aside=False)
"""

from datetime import datetime
from typing import Any

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Forwarded(DataTransferObject):
    """How one message went: delivered at a moment after some attempts, or failed — set aside
    when it never can go."""

    attempts: int
    delivered_at: datetime | None = None
    last_error: str | None = None
    set_aside: bool = False
    """Never tried again: the error says it fails the same way every time."""


class ForwardingHook:
    """Told of every message a forwarding delivered and of every attempt that failed — to stamp
    where it is stored, to count, to alert. Implement the moments you need.

        class Audited(ForwardingHook):
            def after_delivery(self, message, forwarded):
                audit.write(message.id, forwarded.delivered_at, forwarded.attempts)

        Forwarding(hooks=[Audited()])

    Runs on the forwarding's own thread, outside the context the message was published in — a
    unit of work that context held is over by then, so what a hook writes goes through one of its
    own. One that raises is logged; the forwarding goes on.
    """

    def after_delivery(self, message: Any, forwarded: Forwarded) -> None:
        """`message` went out."""

    def after_failure(self, message: Any, forwarded: Forwarded) -> None:
        """An attempt to send `message` failed — the last one when `forwarded.set_aside`."""
