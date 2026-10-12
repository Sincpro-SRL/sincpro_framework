"""A search template: a filter that waits for the text a person typed, filled when it is asked."""

from sincpro_framework.ddd.criteria.domain.expression import Condition, Expression, holds_text
from sincpro_framework.ddd.criteria.domain.values import TEXT
from sincpro_framework.ddd.criteria.domain.walk import mapped


def with_text(expression: Expression | None, text: str) -> Expression | None:
    """A template's filter with the literal in place of every `TEXT`.

        in   Any([code starts with TEXT, active = true]), "1.2"
        out  Any([code starts with "1.2", active = true])
        in   All([name like TEXT, active = true]), ""
        out  active = true                        a blank literal asks nothing of the text

    A condition holding `TEXT` — alone or in a list — takes the literal; with no literal, it is
    dropped. The template itself is never changed.
    """

    def filled(condition: Condition) -> Condition | None:
        if not holds_text(condition):
            return condition
        if not text:
            return None
        value = condition.value
        return condition.model_copy(
            update={
                "value": (
                    [text if one == TEXT else one for one in value]
                    if isinstance(value, list)
                    else text
                )
            }
        )

    return mapped(expression, filled)
