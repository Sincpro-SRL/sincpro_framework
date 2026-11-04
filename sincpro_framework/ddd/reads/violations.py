"""The stored records that break a rule — what a rule added today finds among the rows written
before it, so they are fixed before the next save refuses them.

    found = violations_of(repository, Sale, Sale.delivery_when_confirmed)
    found[0]    ->  Violation(record_id="0192…", message="A confirmed sale needs a delivery date",
                              path=("delivery",))
    DataFrame.from_rows([one.model_dump() for one in found])      # to a report

Context: the records are read page by page, as the criteria pages them, with every relation the
entity's checks watch, and the check runs over each one exactly as a save runs it; nothing is
written and nothing is computed. A rule a database must answer by itself — filtered, counted or
grouped over millions of rows — is a `computed(sql=…, python=…)` field the check reads.
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import CountMode, Criteria
from sincpro_framework.ddd.entity.entity_collection import identity_name
from sincpro_framework.ddd.entity.entity_meta import checks_of
from sincpro_framework.ddd.reads.reads import with_computed_relations
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Violation(DataTransferObject):
    """A stored record that breaks a rule: which one, what the rule says, and where it points."""

    record_id: str
    message: str
    path: tuple[str | int, ...] = ()


def violations_of(
    repository: Any,
    entity: type,
    check: Callable[..., Any],
    criteria: Criteria | None = None,
) -> list[Violation]:
    """Every record of `entity` the criteria reads — every one, when none is given — that breaks
    `check`, an `@check` method of the entity.

        violations_of(repository, Sale, Sale.delivery_when_confirmed)
            ->  [Violation(record_id="0192…", message="…", path=("delivery",))]

    1. `check` names an `@check` of `entity` by its method; anything else is refused.
    2. The records are read page by page, with the relations the entity's checks watch, so a
       rule over lines sees them whole.
    3. The check runs as the record's own method — a subclass's override heard; an
       `InvalidValue` is a violation pointing where the failure does, at the first field the
       check watches when it gives no path.
    Final: the violations, in the order the records were read.
    """
    name = getattr(check, "__name__", "")
    declared = next((one for one in checks_of(entity) if one.method == name), None)
    if declared is None:
        raise exceptions.ProgrammingError(
            f"{entity.__name__} has no @check {name or check!r}: violations_of takes one of its "
            f"checks, as in violations_of(repository, {entity.__name__}, "
            f"{entity.__name__}.<method>)"
        )
    asked = with_computed_relations(
        entity, (criteria or Criteria()).model_copy(update={"count": CountMode.NONE})
    )
    key = identity_name(entity)
    found: list[Violation] = []
    cursor: str | None = None
    while True:
        page = repository.search(entity, asked.resuming_from(cursor))
        for record in page.items:
            try:
                getattr(record, declared.method)()
            except exceptions.InvalidValue as error:
                found.append(
                    Violation(
                        record_id=str(getattr(record, key)),
                        message=str(error),
                        path=error.path or declared.watches[:1],
                    )
                )
        cursor = page.cursor
        if cursor is None:
            return found
