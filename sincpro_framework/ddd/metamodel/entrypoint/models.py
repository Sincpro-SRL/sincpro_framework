"""The model of an entity and its description, for a project and for the framework's own readers.

model_of(Sale).computations
model_of(Sale).affects["customer"]
description_of(Sale)                                   as memory holds it
description_of(Sale, StoreFacts(identity="id", …))     as a store that knows more holds it
contract_of(Sale)                                      what a client is published
"""

from typing import Any

from sincpro_framework.ddd.entity.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import identity_name
from sincpro_framework.ddd.entity.entity_meta import Meta, related_class
from sincpro_framework.ddd.metamodel.domain.facts import StoreFacts
from sincpro_framework.ddd.metamodel.domain.model import Model
from sincpro_framework.ddd.metamodel.infrastructure.registry import models
from sincpro_framework.ddd.metamodel.services.contract import rendered
from sincpro_framework.ddd.metamodel.services.description import (
    described,
    held_relations,
)


def model_of(declared: type) -> Model:
    """The compiled model of `declared` — compiled the first time, the same object after."""
    return models.of(declared)


def forget_models(*declared: type) -> None:
    """The models of `declared` — every model, when none is named — compiled again when next read:
    what a mapping or an extension calls once it changed what a class declares.

        forget_models(Sale)    a relation was installed on Sale
        forget_models()        an extension overlaid fields of several entities
    """
    models.forget(*declared)


def description_of(declared: type, facts: StoreFacts | None = None) -> Meta:
    """The description of `declared` as a store with these `facts` holds it — with none, as the
    annotations say it and with no identity: a value object, or a check a store did not ask for.

        description_of(Size)                                      ->  Meta(identity="", …)
        description_of(Sale, StoreFacts(identity="id"))           ->  Meta(identity="id", …)

    What a store says is learned (`models.learn`): the published contract says what the store
    that keeps the entity knows, not only what its annotations say.
    """
    if facts is not None:
        models.learn(declared, facts)
    return described(
        models.of(declared), facts if facts is not None else StoreFacts(identity="")
    )


def contract_of(declared: type) -> dict[str, Any]:
    """The published contract of `declared`: its JSON Schema 2020-12 with one `x-sincpro` per
    schema and per property, and the entities its relations point at under `$defs`.

        contract_of(Sale)
            ->  {"type": "object", "title": "Sale", "properties": {…},
                 "x-sincpro": {"entity": "Sale", "identity": "id", "version": "3f9c…", …},
                 "$defs": {"Customer": {…}, "SaleLine": {…}, "Discount": {…}}}

    1. The entities reached: `declared`, then each entity a relation of one reached points at,
       once each.
    2. Each described as the store that keeps it last said (`models.facts_of`) — or, when no store
       has described it yet, as its annotations say: found by its first field, a field holding
       another entity a relation no filter crosses —, and rendered with the fingerprint of its
       model as its version; each shape a record holds inside written once, by its class's name
       (`shapes`), the entities' names taken from the start.
    Final: the schema of `declared`; the entities and the shapes beside it under `$defs`.
    """
    reached: list[type] = [declared]
    for one in reached:
        for annotation in models.of(one).annotations.values():
            kind, _ = related_class(annotation)
            if isinstance(kind, type) and issubclass(kind, Entity) and kind not in reached:
                reached.append(kind)
    shapes: dict[str, Any] = {one.__name__: {} for one in reached}
    schemas = {
        one.__name__: rendered(
            described(
                models.of(one),
                models.facts_of(one)
                or StoreFacts(
                    identity=identity_name(one),
                    relations=held_relations(models.of(one), False),
                ),
            ),
            models.of(one).fingerprint,
            shapes,
        )
        for one in reached
    }
    contract = schemas.pop(declared.__name__)
    beside = {**shapes, **schemas}
    beside.pop(declared.__name__)
    if beside:
        contract["$defs"] = beside
    return contract
