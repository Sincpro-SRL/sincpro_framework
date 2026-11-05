"""The metamodel: every entity described once, into one immutable model every reader takes its
part from — persistence, previews and saves, the description a client gets.

    model = model_of(Sale)
    model.computations, model.checks, model.owned, model.affects, model.fingerprint
    description_of(Sale)          what a client and a criteria are checked against
    contract_of(Sale)             what a client is published: JSON Schema with `x-sincpro`

The specification is `docs/spec/model.md`.
"""

from sincpro_framework.ddd.metamodel.domain.declaration import Declaration
from sincpro_framework.ddd.metamodel.domain.facts import StoredField, StoreFacts
from sincpro_framework.ddd.metamodel.domain.model import Model
from sincpro_framework.ddd.metamodel.entrypoint.models import (
    contract_of,
    description_of,
    forget_models,
    model_of,
)

__all__ = [
    "Declaration",
    "Model",
    "StoreFacts",
    "StoredField",
    "contract_of",
    "description_of",
    "forget_models",
    "model_of",
]
