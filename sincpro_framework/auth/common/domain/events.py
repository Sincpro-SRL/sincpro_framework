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
from sincpro_framework.exceptions import FailureKind

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
    kind: FailureKind = FailureKind.PERMISSION_DENIED
    """`FailureKind.UNAUTHENTICATED` or `FailureKind.PERMISSION_DENIED`."""
    reason: str = ""
    requirement: str = ""
    """What was not met — empty when nobody was known."""
    tenant: str | None = None


USE_CASE = "auth.use_case"
"""The `entity_type` of a fact about a use case — `entity_id` its Command's dotted name."""


@dataclass(kw_only=True)
class RequirementAdded(DeliverableEventMixin, AuthEvent):
    """A use case requires more from now on — what was added at runtime, as it now stands."""

    name = "auth.requirement.v1.added"
    label: dict[str, str] = field(
        default_factory=lambda: {
            "default": "Requirement added",
            "es": "Se agregó un requisito",
        }
    )
    tenant: str | None = None
    """The tenant it holds for — `None`: every tenant."""
    permissions: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class RequirementRemoved(DeliverableEventMixin, AuthEvent):
    """What was added at runtime to a use case removed — its code's declaration holds alone."""

    name = "auth.requirement.v1.removed"
    label: dict[str, str] = field(
        default_factory=lambda: {
            "default": "Requirement removed",
            "es": "Se quitó un requisito",
        }
    )
    tenant: str | None = None
