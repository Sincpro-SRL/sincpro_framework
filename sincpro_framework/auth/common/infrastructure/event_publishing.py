"""Handing an auth event to the publisher a component was given — stamped with who did it and
the flow it belongs to, and never undoing what was already written.

    publish_event(self.publisher, RoleAssigned(entity_id="user:ana", role="billing.manager"))
"""

import dataclasses

from sincpro_framework.auth.common.domain.events import AuthEvent, EventPublisher
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.context import context_executions
from sincpro_framework.sincpro_logger import logger


def publish_event(publisher: EventPublisher | None, event: AuthEvent) -> None:
    """Publish `event` — nothing without a publisher.

    Context: who did it is the identity in play — verified by a provider, or opened by
    `security.as_identity` / `as_system` — never the context's `user_id`, which a caller's own
    headers may carry. The change it tells of is already written, so a publisher that fails is
    logged with the whole event — the audit can be replayed from the log — and never raised over
    it: an error would tell the caller it did not happen, and leave the rest of the change
    undone. A broker's outage is the `Publisher(..., forwarding=True)`'s to wait out.
    """
    if publisher is None:
        return
    identity = current_identity()
    actor = event.actor or (None if identity.is_anonymous else identity.subject)
    stamped = dataclasses.replace(event, actor=actor, **context_executions.chain_for(event))
    try:
        publisher.publish(stamped)
    except Exception as failed:
        logger.error(
            f"{stamped.name} was not published: {failed!r}",
            published=stamped.as_json(),
        )
