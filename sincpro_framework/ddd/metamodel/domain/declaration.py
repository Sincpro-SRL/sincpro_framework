"""What an entity says about itself, as data: the metamodel compiler's input.

    declaration_of(Sale)   ->  Declaration(name="Sale", annotations={…}, computations=(…), …)

A class is one way to get a declaration — its annotations, `field(metadata=…)`, `@compute`,
`@check`, `computed(…)`, `presentation` and `DEFAULT_*`, each read once. Something that is not a
class (a field a tenant adds, a model another context published) is another way to build the same
value, so the compiler never has to know where a declaration came from.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.ddd.entity.computed import ComputedField
from sincpro_framework.ddd.entity.entity_meta import Check, Computation, Presented


@dataclass(frozen=True)
class Declaration:
    """Everything an entity declares, with field names in place of lambdas.

    `entity` is the class that runs the declaration's methods — a computation's, a check's — and
    the record a store builds; `title` is its name in every language it declares;
    `framework_fields` are the fields the framework writes (`id`, `version`, a mixin's), never a
    form's.
    """

    entity: type
    name: str
    title: Mapping[str, str]
    annotations: Mapping[str, Any]
    framework_fields: frozenset[str]
    computations: tuple[Computation, ...]
    checks: tuple[Check, ...]
    computed: Mapping[str, ComputedField]
    presented: Presented
    defaults: Mapping[str, Any]
    labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    helps: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    overlays: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """What the modules that extend the entity changed about how each field is shown — a label,
    a hint —, merged from the base class down, so the deepest extension wins."""
