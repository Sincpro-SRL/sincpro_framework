"""What an event opts into, the way an entity opts into `ArchivableMixin`.

DeliverableEventMixin      heard beyond this bounded context, and when it went out
"""

import json
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sincpro_framework.ddd.entity import utc_now

DELIVERY_FIELDS = frozenset({"delivered_at", "delivery"})
"""When a deliverable event went out and how — stored with the event, never sent with it."""

VERSIONED = re.compile(r"\.v\d+\.")
"""A wire name with its contract's version in it: `billing.invoice.v1.paid`."""


@dataclass(kw_only=True)
class DeliverableEventMixin:
    """Marks the events heard beyond this bounded context — by a broker, by other services — and
    stores when one went out: the moment it happened is `created_at`, the moment it was delivered
    is another fact, the one an audit asks for.

        @dataclass(kw_only=True)
        class InvoicePaid(DeliverableEventMixin, BillingDomainEvent):
            name = "billing.invoice.v1.paid"
            invoice_id: str = ""

    | Field | What it stores |
    |---|---|
    | `delivered_at` | when it was delivered — empty while it is not |
    | `delivery` | what a person reads: `{"attempts": 2, "last_error": "…"}` |

    The mark is what a repository that publishes on commit hands on by default
    (`publish_on_commit=`), and that repository stamps the stored event once it went out or
    failed to. A publisher takes any message; the mark is how this framework sees a contract, not
    a condition to publish.

    **The marked class is a contract.** A domain event carries what this context knows and may
    change with it; a delivered event is what other contexts depend on, so it carries only what
    they need and changes by a new version of its name. Declaring one without an explicit,
    versioned `name` is warned about: renaming the class would rename the event under its
    consumers, and they would stop hearing it without an error.

    **Delivery state stays home.** The two fields are written on the event's row and never in
    what is published: `as_json()` leaves them out.
    """

    delivered_at: datetime | None = None
    delivery: dict[str, Any] = field(default_factory=dict)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Warns, at class definition, about a delivered event without its own versioned name.

            name = "billing.invoice.v1.paid"     quiet
            no name, or name = "InvoicePaid"     warned: a rename would break its consumers

        Context: a warning and not a refusal — the event still works; it is the contract that
        is fragile. Only the class's own `name` counts, never an inherited one.
        """
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
        """How many times delivery was tried, delivered or not."""
        return int(self.delivery.get("attempts", 0))

    def delivered(self, at: datetime | None = None, attempts: int = 1) -> None:
        """It went out at `at`, after `attempts` tries."""
        self.delivered_at = at or utc_now()
        self.delivery = {**self.delivery, "attempts": self.delivery_attempts + attempts}

    def failed(self, error: str, attempts: int = 1) -> None:
        """It did not go out, after `attempts` tries; `error` says why."""
        self.delivery = {
            **self.delivery,
            "attempts": self.delivery_attempts + attempts,
            "last_error": error,
        }

    def as_json(self) -> str:
        """The event as other contexts receive it: without when its delivery happened."""
        written = json.loads(super().as_json())  # type: ignore[misc]
        return json.dumps(
            {key: value for key, value in written.items() if key not in DELIVERY_FIELDS}
        )
