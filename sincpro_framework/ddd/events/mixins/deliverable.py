"""What an event opts into, the way an entity opts into `ArchivableMixin`.

DeliverableEventMixin      delivered beyond this bounded context, and whether it was
"""

import json
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sincpro_framework.ddd.entity import utc_now

DELIVERY_FIELDS = frozenset({"delivered_at", "next_delivery_at", "delivery"})
"""Where a deliverable event's delivery stands — kept with the event, never sent with it."""

VERSIONED = re.compile(r"\.v\d+\.")
"""A wire name with its contract's version in it: `billing.invoice.v1.paid`."""


@dataclass(kw_only=True)
class DeliverableEventMixin:
    """Marks the events delivered beyond this bounded context — to a broker, to other services —
    and keeps where their delivery stands (the transactional outbox: Richardson's polling
    publisher; Microsoft's eShop `IntegrationEventLogEntry`).

        @dataclass(kw_only=True)
        class InvoicePaid(DeliverableEventMixin, BillingDomainEvent):
            name = "billing.invoice.v1.paid"
            invoice_id: str = ""

    | Field | What it keeps |
    |---|---|
    | `delivered_at` | when it was delivered — empty while it is not |
    | `next_delivery_at` | when the relay may try it — now, when it is made; later, after a failure; empty once parked |
    | `delivery` | what a person reads: `{"attempts": 2, "last_error": "…", "parked": False}` |

    **The marked class is a contract.** A domain event carries what this context knows and may
    change with it; a delivered event is what other contexts depend on, so it carries only what
    they need and changes by a new version of its name. Declaring one without an explicit,
    versioned `name` is warned about: renaming the class would rename the event under its
    consumers, and they would stop hearing it without an error.

    **Delivery state stays home.** The three fields are written on the event's row and never in
    what is published: `as_json()` leaves them out.
    """

    delivered_at: datetime | None = None
    next_delivery_at: datetime | None = field(default_factory=utc_now)
    delivery: dict[str, Any] = field(default_factory=dict)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = cls.__dict__.get("name")
        if not isinstance(declared, str) or not VERSIONED.search(declared):
            warnings.warn(
                f"{cls.__name__} is delivered to other contexts but its wire name is "
                f"{getattr(cls, 'name', cls.__name__)!r}: declare an explicit, versioned name "
                '(name = "billing.invoice.v1.paid") so a rename never breaks its consumers',
                stacklevel=2,
            )

    @property
    def delivery_attempts(self) -> int:
        return int(self.delivery.get("attempts", 0))

    def delivered(self, at: datetime | None = None) -> None:
        """It went out."""
        self.delivered_at = at or utc_now()
        self.next_delivery_at = None
        self.delivery = {**self.delivery, "attempts": self.delivery_attempts + 1}

    def failed(self, error: str, next_delivery_at: datetime) -> None:
        """It did not go out; it is tried again at `next_delivery_at`."""
        self.next_delivery_at = next_delivery_at
        self.delivery = {
            **self.delivery,
            "attempts": self.delivery_attempts + 1,
            "last_error": error,
        }

    def parked(self, error: str) -> None:
        """Given up on: kept with why, and never tried again until somebody replays it."""
        self.next_delivery_at = None
        self.delivery = {
            **self.delivery,
            "attempts": self.delivery_attempts + 1,
            "last_error": error,
            "parked": True,
        }

    def replayed(self, at: datetime | None = None) -> None:
        """Tried again from now on, its attempts back to zero."""
        self.next_delivery_at = at or utc_now()
        self.delivery = {"attempts": 0}

    def as_json(self) -> str:
        """The event as other contexts receive it: without where its delivery stands."""
        written = json.loads(super().as_json())  # type: ignore[misc]
        return json.dumps(
            {key: value for key, value in written.items() if key not in DELIVERY_FIELDS}
        )
