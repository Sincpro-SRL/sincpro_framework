"""A record of any bounded context, named by one text: `billing.Invoice:0192…`.

    ref = EntityRef.of(invoice)                       # inside a use case of billing
    str(ref)                                          ->  "billing.Invoice:0192…"
    EntityRef.parse("billing.Invoice:0192…")          ->  EntityRef("billing", "Invoice", "0192…")

    class CommandPaymentConfirmed(DataTransferObject):
        subject: EntityRef                            # a text on the wire, checked on the way in

What travels in a payload that comes back later — a payment's metadata, a webhook, a job, a
notification — so whoever receives it reads the record by its own `Get`
(`sincpro_framework.references.EntityRefs`) without knowing its bus or its route. The bounded
context and the entity are named as a `Reference` names a relation (`tenancy.Tenant`); the key is the
one `Get` finds the record by (`DEFAULT_GET_ID`, its identity by default).

Context: a ref is kept outside the code — in a provider, a queue, a log — so it names the entity
by its class's name, as the contract does: renaming the class renames its refs, its contract and
its relations alike.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic_core import core_schema

from sincpro_framework.context import use_context
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity.entity_collection import identity_name

FORMAT = "bounded_context.Entity:key"
"""How a ref is written, as a refusal names it."""

PATTERN = r"^[^:]+\.[A-Za-z_][A-Za-z0-9_]*:.+$"
"""What a ref looks like, as its JSON Schema publishes it."""


@dataclass(frozen=True)
class EntityRef:
    """One record, by its bounded context, its entity and its key."""

    bounded_context: str
    entity: str
    key: str

    def __str__(self) -> str:
        """`billing.Invoice:0192…`."""
        return f"{self.bounded_context}.{self.entity}:{self.key}"

    @classmethod
    def parse(cls, text: str) -> "EntityRef":
        """The ref a text writes — refused (`ValueError`) when it is not one.

            parse("billing.Invoice:0192…")      ->  EntityRef("billing", "Invoice", "0192…")
            parse("billing.Invoice:F:7")        ->  EntityRef("billing", "Invoice", "F:7")
            parse("Invoice:0192…")              ->  raises ValueError, no bounded context

        Context: the key is everything after the first `:` — a key may hold one; a bounded
        context and an entity never do.
        """
        named, separator, key = text.partition(":")
        bounded_context, dot, entity = named.rpartition(".")
        if not (separator and dot and bounded_context and entity.isidentifier() and key):
            raise ValueError(f"{text!r} is no entity ref: write {FORMAT}")
        return cls(bounded_context, entity, key)

    @classmethod
    def of(cls, record: Any, bounded_context: str | None = None) -> "EntityRef":
        """The ref of `record`: its bounded context — the bus running now, unless named —, its
        class and its key.

            EntityRef.of(invoice)                       # in billing  ->  billing.Invoice:0192…
            EntityRef.of(account)                       # Account keyed by `code`  ->  ….Account:1.2.3
            EntityRef.of(invoice, bounded_context="billing")   # outside any bus

        Outside a bus and with no bounded context named, it is refused: a ref that names no
        context names no record.
        """
        if bounded_context is None:
            bus = use_context().bus
            bounded_context = bus.label if bus is not None else ""
        if not bounded_context:
            raise exceptions.ProgrammingError(
                f"EntityRef.of({type(record).__name__}) runs outside a bus: name its bounded "
                f'context — EntityRef.of(record, bounded_context="billing")'
            )
        entity = type(record)
        key = entity.DEFAULT_GET_ID() or identity_name(entity)
        return cls(bounded_context, entity.__name__, str(getattr(record, key)))

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Read from a ref or its text, written as its text."""

        def read(value: Any) -> "EntityRef":
            return value if isinstance(value, EntityRef) else cls.parse(str(value))

        return core_schema.no_info_plain_validator_function(
            read, serialization=core_schema.plain_serializer_function_ser_schema(str)
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _schema: core_schema.CoreSchema, _handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        """A text in the ref's form, so a generated client types it as one."""
        return {
            "type": "string",
            "format": "entity-ref",
            "pattern": PATTERN,
            "examples": ["billing.Invoice:01a1e6f2c0b37a4e9b1d2c3e4f5a6b7c"],
        }
