"""`Resolution`: a filter's dynamic values read into the literals they stand for."""

from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.services import dynamic_values


class Resolution:
    """Reading a filter's dynamic values, on the server, with its own clock and context.

    Resolution.has_dynamic_values(Condition(field="due", operator=Operator.LT, value=Today()))
        ->  True
    Resolution.resolve(that, ResolutionContext(now=datetime(2026, 10, 12, tzinfo=UTC)))
        ->  due < 2026-10-12
    """

    @staticmethod
    def has_dynamic_values(expression: Expression | None) -> bool:
        """Whether any condition of the tree holds a value that is read when it is used, alone
        or as an item of a list."""
        return dynamic_values.has_dynamic_values(expression)

    @staticmethod
    def resolve(
        expression: Expression | None, resolution: ResolutionContext
    ) -> Expression | None:
        """The same filter with every dynamic value replaced by the literal it stands for; a
        tree with nothing dynamic is handed back as it came."""
        return dynamic_values.resolve(expression, resolution)
