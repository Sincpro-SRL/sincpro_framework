"""What auth says happened — the facts an audit trail is made of, handed to a publisher the
project chose. Nothing is published without one.

    auth = AccessControl[P](providers=[...], publisher=audit)     # the framework's `Publisher`
    a refused call  ->  AccessRefused(entity_id="user:ana", use_case="CommandApprove",
                                      kind="permission_denied", requirement="billing.approve")

Context: every event says who it is about (`entity_type` `auth.subject` or `auth.tenant`, and
`entity_id`), who did it (`actor` — the identity in play, as a provider verified it), and the
flow it belongs to (`correlation_id`, `causation_id`). Names are versioned —
`auth.<what>.v1.<fact>` — because another service depends on them: a platform's audit, a cache
invalidated elsewhere. Each door declares its own facts beside its own code; here the base, the
port and what the guard says.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sincpro_framework.ddd.events import DeliverableEventMixin, DomainEvent

SUBJECT = "auth.subject"
"""The `entity_type` of a fact about a person, a service or an API key — `entity_id` its subject."""
TENANT = "auth.tenant"
"""The `entity_type` of a fact about a tenant — `entity_id` the tenant."""


class EventPublisher(Protocol):
    """Where auth hands what happened — the framework's `Publisher`, or anything that publishes."""

    def publish(self, event: Any, /) -> Any:
        """Hand `event` on — positional, as `Publisher.publish` takes it."""
        ...


@dataclass(kw_only=True)
class AuthEvent(DomainEvent):
    """Every fact auth publishes — what a project maps to its audit table, or subscribes to."""


@dataclass(kw_only=True)
class AccessRefused(DeliverableEventMixin, AuthEvent):
    """A call was refused before its use case or hook ran — what it asked, and why."""

    name = "auth.access.v1.refused"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Access refused", "es": "Se rechazó el acceso"}
    )
    use_case: str = ""
    """The Command's name, or `Hook.moment` for a hook."""
    kind: str = ""
    """`unauthenticated` or `permission_denied`."""
    reason: str = ""
    requirement: str = ""
    """What was not met — empty when nobody was known."""
    tenant: str | None = None
