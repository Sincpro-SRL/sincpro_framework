"""How a form shows each field of an entity: hints a client builds from, never rules.

    @dataclass
    class Invoice(Entity):
        ...
        presentation = Presentation["Invoice"](
            readonly=lambda i: i.number,
            required=lambda i: i.partner_id,
            readonly_when=lambda i: When(
                Condition.on(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount
            ),
            visible_when=lambda i: When(
                Condition.on(i.payment, Operator.EQ, Payment.CARD), i.card_reference
            ),
        )

Each lambda names its fields through the entity, so a type checker reads `i.number` against
`Invoice` and a rename reaches it. It runs once, when the class is described, against `Fields`:
a stand-in whose attributes are the field names. It never sees a record.

**These are hints, never rules.** `Meta` publishes them so a screen shows an asterisk, greys
a field or hides it without being told twice; a component that says otherwise wins. The
framework checks none of them: a save of a confirmed invoice with another partner goes
through. A rule the server keeps is the project's own hook (`before_save`), which may read
the same condition from `presentation_of(Invoice)`.

How the entity is read — its key, what a record brings, the order, the display and how a text
finds it — is not here: it is the entity's own `DEFAULT_*` class methods (`Entity`).
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from sincpro_framework.ddd.criteria import Expression
from sincpro_framework.exceptions import ProgrammingError


class FieldRef(str):
    """A field as a presentation lambda reads it: `a.code` is `FieldRef("code")`. It is the text
    of the name, so it goes wherever a criteria takes a field."""


class Fields:
    """The stand-in a declaration's lambda runs against (`presentation`, `derivations`). An
    unknown name fails here, naming the class, so a typo is refused when the class is described
    and not on the first empty page.
    """

    def __init__(
        self, owner: str, names: Iterable[str], declaration: str = "presentation"
    ) -> None:
        self._owner = owner
        self._names = frozenset(names)
        self._declaration = declaration

    def __getattr__(self, name: str) -> FieldRef:
        if name not in self._names:
            raise ProgrammingError(
                f"{self._owner}.{self._declaration} names {name}, "
                f"which is not a field of {self._owner}"
            )
        return FieldRef(name)


def field_name(value: object, form: str) -> str:
    """The name behind a `FieldRef`; anything else is refused, showing the form it takes.

    in      FieldRef("number"), "i.number"    →  out  "number"
    in      "number", "i.number"              →  ProgrammingError: takes a field read through
                                                 the entity, as in lambda a: i.number
    """
    if isinstance(value, FieldRef):
        return str(value)
    raise ProgrammingError(
        f"a presentation takes a field read through the entity, as in lambda a: {form}; "
        f"it was given {value!r}"
    )


@dataclass(frozen=True, init=False)
class When:
    """The fields a hint covers while a condition holds. The condition is a criteria's own:
    `Condition`, `All`, `Any` or `Not`.

    When(Condition.on(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount)
    """

    condition: Expression
    fields: tuple[object, ...]

    def __init__(self, condition: Expression, *fields: object) -> None:
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True)
class Presentation[T]:
    """How a form shows each field: hints a client may override, none of them checked.

    `readonly` and `required` always; `readonly_when`, `required_when` and `visible_when`
    while a condition holds over the record (`When(...)`). Left alone, the fields the framework
    writes (`id`, `version`, …) are read-only and a field with no default that cannot be
    `None` is required.
    """

    readonly: Callable[[T], object] | None = None
    required: Callable[[T], object] | None = None
    readonly_when: Callable[[T], When | Sequence[When]] | None = None
    required_when: Callable[[T], When | Sequence[When]] | None = None
    visible_when: Callable[[T], When | Sequence[When]] | None = None
