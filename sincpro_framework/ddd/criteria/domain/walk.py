"""The one way to rebuild a filter: change its leaves, and let the groups follow.

Everything that turns a filter into another — filling a typed text, resolving a `today`, dropping
what a model cannot answer — decides only what becomes of ONE condition. What happens to the
`all`, `any` and `not` around it is the same every time and lives here.
"""

from collections.abc import Callable

from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
)


def mapped(
    expression: Expression | None, leaf: Callable[[Condition], Expression | None]
) -> Expression | None:
    """The tree with each condition replaced by what `leaf` says, and what is left collapsed.

        in      All([a, b]), leaf = a → a2, b → None
        out     a2                           one part needs no wrapper

    A condition may become another expression (a range is two conditions) or nothing. A group
    that loses all of its parts disappears instead of becoming empty — an empty `all` matches
    everything and an empty `any` nothing, and neither was asked for — and a `not` of nothing is
    nothing.
    """
    match expression:
        case None:
            return None
        case Condition():
            return leaf(expression)
        case All():
            parts = [one for one in (mapped(part, leaf) for part in expression.all) if one]
            return All(all=parts) if len(parts) > 1 else (parts[0] if parts else None)
        case Any_():
            parts = [one for one in (mapped(part, leaf) for part in expression.any) if one]
            return Any_(any=parts) if len(parts) > 1 else (parts[0] if parts else None)
        case Not():
            inner = mapped(expression.negate, leaf)
            return Not(negate=inner) if inner is not None else None
