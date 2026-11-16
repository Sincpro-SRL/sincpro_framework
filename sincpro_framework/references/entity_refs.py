"""The records refs name, each read by its own `Get`, on the bus of its bounded context.

    refs = EntityRefs([billing, auth])                        # the contexts it may read
    refs.resolve(EntityRef.parse("billing.Invoice:0192…"))    ->  Invoice(…)
    refs.resolve_many([invoice_ref, member_ref, gone_ref])    ->  [Invoice(…), Member(…), None]

What a payment, a webhook or a job that comes back with a ref uses to read what it points at,
without knowing which bus or which route answers it.

Context: never past the record's own read. Each ref is answered by the `Get` its bounded context
registered for the entity — its interceptors, its answer — and its guards decide anew for whoever
is calling, as for a request from outside: a use case calling a resolution never lends it its own
permission. A record whose entity no bus here reads by a `Get` cannot be resolved.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from sincpro_framework.auth import security
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity.entity_ref import EntityRef
from sincpro_framework.ddd.reads.reads import Get, GetMany, key_of, named_in_type
from sincpro_framework.use_bus import UseFramework


class EntityRefs:
    """Resolves refs over the bounded contexts it is given."""

    def __init__(self, buses: Iterable[UseFramework]) -> None:
        """The bounded contexts whose records it may read, by their name."""
        self.buses: dict[str, UseFramework] = {bus.name: bus for bus in buses}

    def reads(self, ref: EntityRef) -> tuple[UseFramework, type, type | None]:
        """The bus of `ref`'s bounded context, the `Get` it reads the entity with, and its
        `GetMany` when it registered one — read from what is registered now.

        1. A bounded context it was not given: `AggregateNotFound`, naming the ones it was.
        2. An entity its bus reads by no `Get`: `AggregateNotFound`, saying what is missing.
        Final: the bus and its reads.
        """
        bus = self.buses.get(ref.bounded_context)
        if bus is None:
            raise exceptions.AggregateNotFound(
                f"{ref}: no bounded context {ref.bounded_context!r} resolves here — "
                f"only {', '.join(sorted(self.buses)) or 'none'}"
            )
        get: type | None = None
        many: type | None = None
        for dto in bus.dto_registry.values():
            named = named_in_type(dto)
            if named is None or named[0].__name__ != ref.entity:
                continue
            if get is None and issubclass(dto, Get):
                get = dto
            elif many is None and issubclass(dto, GetMany):
                many = dto
        if get is None:
            raise exceptions.AggregateNotFound(
                f"{ref}: {ref.bounded_context} reads no {ref.entity} by a Get — register one "
                "for its refs to resolve"
            )
        return bus, get, many

    def resolve(self, ref: EntityRef) -> Any:
        """The record `ref` names, as its `Get` answers it — `AggregateNotFound` when there is
        none, refused as its `Get` refuses whoever is calling."""
        bus, get, _ = self.reads(ref)
        with security.as_identity(security.identity):
            answer: Any = bus(get(id=ref.key))
        return getattr(answer, answer.record_field())

    def resolve_many(self, refs: Sequence[EntityRef]) -> list[Any]:
        """The records `refs` name, in the order asked — `None` for one that is not there.

            resolve_many([invoice_ref, member_ref, gone_ref])   ->  [Invoice(…), Member(…), None]

        1. The refs grouped by bounded context and entity, so each entity is read once.
        2. Each group under the guards of its own reads, decided anew: its `GetMany` when the
           context registered one, else its `Get` ref by ref.
        Final: one record or `None` per ref asked; a refusal is raised, as any call raises it.
        """
        found: dict[EntityRef, Any] = {}
        grouped: dict[tuple[str, str], list[EntityRef]] = {}
        for ref in refs:
            grouped.setdefault((ref.bounded_context, ref.entity), []).append(ref)
        for group in grouped.values():
            bus, get, many = self.reads(group[0])
            with security.as_identity(security.identity):
                if many is not None:
                    answer: Any = bus(many(ids=list(dict.fromkeys(one.key for one in group))))
                    key = key_of(named_in_type(get)[0])  # type: ignore[index]
                    held = {
                        str(getattr(one, key)): one
                        for one in getattr(answer, answer.records_field())
                    }
                    found.update({one: held.get(one.key) for one in group})
                    continue
                for one in group:
                    try:
                        answered: Any = bus(get(id=one.key))
                    except exceptions.AggregateNotFound:
                        found[one] = None
                        continue
                    found[one] = getattr(answered, answered.record_field())
        return [found.get(one) for one in refs]
