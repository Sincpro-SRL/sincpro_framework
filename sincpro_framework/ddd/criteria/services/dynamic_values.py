"""Reading a filter's dynamic values: the tree comes in with `today` and `context`, and goes out
with the date and the user.

        in      due < {"$": "today"}                       what was written, and what is stored
        out     due < 2026-10-12                           what the store runs

The tree stays symbolic until it is used. The server resolves — with its own clock, its own
timezone and its own context — so a saved view stays alive, a client cannot say it is another
user, and a key made for a cache is made from what actually ran.

**One instant per resolution**: a `ResolutionContext` reads the clock once, so `today` and `now`
in the same filter agree. Everything is UTC unless the context names a timezone, and a timezone
decides only where a calendar boundary falls: «the start of the day» in La Paz is a UTC instant
before anything is compared.
"""

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Condition,
    Expression,
    Operator,
)
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.values import RESOLVABLE, Period
from sincpro_framework.ddd.criteria.services.reading import item_of


def _resolved(condition: Condition, resolution: ResolutionContext) -> Expression:
    """One condition with its dynamic values read; a period becomes the two bounds it stands for."""
    value = condition.value
    if isinstance(value, Period):
        if condition.operator is not Operator.BETWEEN:
            raise exceptions.InvalidCriteria(
                f"a period is a range and takes the operator 'between'; "
                f"'{condition.field}' asked it with '{condition.operator.value}'"
            )
        start, end = value.range(resolution)
        return All(
            all=[
                Condition(field=condition.field, operator=Operator.GTE, value=start),
                Condition(field=condition.field, operator=Operator.LT, value=end),
            ]
        )
    if isinstance(value, list):
        read = [item_of(one, condition.field, resolution) for one in value]
        return condition.model_copy(update={"value": read})
    return condition.model_copy(update={"value": item_of(value, condition.field, resolution)})


def has_dynamic_values(expression: Expression | None) -> bool:
    """Whether any condition of the tree holds a value that is read when it is used, alone or as
    an item of a list."""
    if expression is None:
        return False
    return any(
        isinstance(one.value, RESOLVABLE)
        or (
            isinstance(one.value, list)
            and any(isinstance(item, RESOLVABLE) for item in one.value)
        )
        for one in expression.conditions()
    )


def resolve(
    expression: Expression | None, resolution: ResolutionContext
) -> Expression | None:
    """The same filter with every dynamic value replaced by the literal it stands for.

        in      All([due < today, owner_id = context.user_id]), user 'u-7' on 2026-10-12
        out     All([due < 2026-10-12, owner_id = 'u-7'])

    A tree with nothing dynamic is handed back as it came. What cannot be read — a context key
    that is absent, a period asked with `=` — is refused naming it, because a filter that quietly
    dropped the condition would bring back more than was asked for.
    """
    if expression is None or not has_dynamic_values(expression):
        return expression
    return expression.mapped(lambda condition: _resolved(condition, resolution))
