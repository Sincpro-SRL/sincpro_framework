"""Handing an auth event to the publisher a component was given — stamped with who did it and
the flow it belongs to.

    announce(self.publisher, RoleAssigned(entity_id="user:ana", role="billing.manager"))
"""

import dataclasses

from sincpro_framework.auth.domain.events import AuthEvent, EventPublisher
from sincpro_framework.auth.infrastructure.security_context import current_identity
from sincpro_framework.context import context_executions


def announce(publisher: EventPublisher | None, event: AuthEvent) -> None:
    """Publish `event` — nothing without a publisher.

    Context: who did it is the identity in play — verified by a provider, or opened by
    `security.as_identity` / `as_system` — never the context's `user_id`, which a caller's own
    headers may carry: an audit names who was proved, not who was claimed. Nobody known is
    `None`. The flow is the execution's, when there is one.
    """
    if publisher is None:
        return
    identity = current_identity()
    actor = event.actor or (None if identity.is_anonymous else identity.subject)
    publisher.publish(
        dataclasses.replace(event, actor=actor, **context_executions.chain_for(event))
    )
