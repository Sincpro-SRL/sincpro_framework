"""How rows are ordered: a field, ascending or descending."""

from sincpro_framework.ddd.criteria.domain.strict import Strict


# No class docstring: pydantic publishes it as the description in the committed
# `criteria.schema.json` contract, which the TypeScript package copies.
# One key of an ordering: a field, ascending unless `descending` —
# Sort(field="registered_at", descending=True) is written "-registered_at".
class Sort(Strict):
    field: str
    descending: bool = False

    def __str__(self) -> str:
        """The URL spelling `parse_order` reads back: `"-registered_at"`, `"name"`."""
        return f"-{self.field}" if self.descending else self.field


def parse_order(raw: str) -> tuple[Sort, ...]:
    """Reads an ordering written the way a URL carries it.

        in      "-registered_at,name"
        split   ["-registered_at", "name"]      on commas, trimmed
        out     (Sort(registered_at, descending), Sort(name, ascending))

    >>> parse_order("-registered_at,name")
    (Sort(field='registered_at', descending=True), Sort(field='name', descending=False))
    >>> parse_order(" -a , , b ")                # blanks and empty parts are ignored
    (Sort(field='a', descending=True), Sort(field='b', descending=False))
    >>> parse_order("")
    ()
    """
    return tuple(
        Sort(field=part.lstrip("-"), descending=part.startswith("-"))
        for part in (piece.strip() for piece in raw.split(","))
        if part
    )
