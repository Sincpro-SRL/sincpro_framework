"""One entity, compiled: its declaration and what follows from it, worked out once.

    model = model_of(Sale)
    model.computations          the computations, in the order they run
    model.owned                 the fields holding records a pass over the sale runs first
    model.affects["customer"]   ("payment_term",)   what a form's change of the customer recomputes
    model.fingerprint           the same text in every process that runs the same code

Context: a model never changes. A change to what an entity declares compiles a new model, with
another fingerprint, and the old one is let go.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sincpro_framework.ddd.entity.computed import ComputedField
from sincpro_framework.ddd.entity.entity_meta import Check, Computation, Presented
from sincpro_framework.ddd.metamodel.domain.declaration import Declaration


@dataclass(frozen=True)
class Model:
    """The compiled entity every reader takes its part from.

    What the entity declared, as the declaration says it:

    - `entity` runs the declaration's methods; `name` is how it is called, `title` how a screen
      calls it in each language;
    - `annotations`, each field's type; `framework_fields`, the ones the framework writes;
    - `computations` in the order they run, `checks` in the order they were declared, `computed`
      the fields a database answers, `presented` its `DEFAULT_*` answers and form hints,
      `defaults` what a new record starts with, `labels` and `helps` how a screen words each
      field, `overlays` what the modules that extend it changed about how a field is shown.

    What follows from it, worked out by the compiler:

    - `owned`, the fields holding records a pass runs first — those its computations read or its
      checks watch, and the lists of records that compute or check something themselves;
    - `affects`, for each field a form may change, the computed fields that change after it;
      `judged_by`, the checks that judge it — together, whether a change needs the server;
    - `formulas`, for each computed field whose computation the language writes down — what its
      method returns, as a formula (`Formulas`) — so a client computes it as the server does;
    - `fingerprint`, the declaration's content hashed: equal wherever the code is the same.
    """

    declaration: Declaration
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
    labels: Mapping[str, Mapping[str, str]]
    helps: Mapping[str, Mapping[str, str]]
    overlays: Mapping[str, Mapping[str, Any]]
    owned: tuple[str, ...]
    formulas: Mapping[str, Any]
    affects: Mapping[str, tuple[str, ...]]
    judged_by: Mapping[str, tuple[str, ...]]
    fingerprint: str
