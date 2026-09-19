"""Who wrote the record, beside when."""

from dataclasses import dataclass


@dataclass(kw_only=True)
class AuditedMixin:
    """Who wrote the record, beside when: a mixin an aggregate opts into.

        @dataclass
        class Invoice(AuditedMixin, Entity):
            number: str

    Nobody writes these two. The adapter stamps them on every flush from the actor the
    `Database` was given — usually `lambda: bus.current_context().get("user.id")`, the same
    id the bus already carries through the call. Without an actor they stay `None`, and the
    aggregate works unchanged.

    The columns go on the table with `audit_columns()`, beside `entity_columns()`.
    """

    created_by: str | None = None
    updated_by: str | None = None
