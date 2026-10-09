"""How an entity is read: the field shown beside its identity, how a literal finds it, the order
of a list and what a detail brings.

    @dataclass
    class Account(Entity):
        code: str
        name: str = ""
        lines: list[Line] = field(default_factory=list)

        presentation = Presentation["Account"](
            display=lambda a: a.name,
            search=lambda a: (Match.equal(a.code), Match.prefix(a.code), Match.contains(a.name)),
            order=lambda a: (a.code,),
            detail=lambda a: (a.code, a.name, Expand(a.lines, 300)),
        )

Each lambda names its fields through the entity, so a type checker reads `a.code` against
`Account` and a rename reaches it. It runs once, when the class is described, against
`Fields`: a stand-in whose attributes are the field names. It never sees a record.

Nothing declared is a declaration too: a field called `name` is the display, and a literal
finds it by containment. `describe` publishes the result on `Meta`; `matching` and `detail_of`
turn it into the criteria a Feature hands to `search` and `get`.

It also says how a form shows each field, as defaults a client builds from:

        presentation = Presentation["Invoice"](
            readonly=lambda i: (i.number,),
            required=lambda i: (i.partner_id,),
            readonly_when=lambda i: (
                When(Is(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount),
            ),
            visible_when=lambda i: (
                When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
            ),
        )

**These are hints, never rules.** `Meta` publishes them so a screen shows an asterisk, greys
a field or hides it without being told twice; a component that says otherwise wins. The
framework checks none of them: a save of a confirmed invoice with another partner goes
through. A rule the server keeps is the project's own hook (`before_save`), which may read
the same condition from `presentation_of(Invoice)`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from sincpro_framework.ddd.criteria import Condition, Operator
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.sincpro_abstractions import DataTransferObject


@dataclass(frozen=True)
class FieldRef:
    """A field as a presentation lambda reads it: `a.code` is `FieldRef("code")`."""

    name: str


class Fields:
    """The stand-in a presentation lambda runs against. An unknown name fails here, naming the
    class, so a typo is refused when the class is described and not on the first empty page.
    """

    def __init__(self, owner: str, names: Iterable[str]) -> None:
        self._owner = owner
        self._names = frozenset(names)

    def __getattr__(self, name: str) -> FieldRef:
        if name not in self._names:
            raise ContractViolation(
                f"{self._owner}.presentation names {name}, which is not a field of {self._owner}"
            )
        return FieldRef(name)


def field_name(value: object, form: str) -> str:
    """The name behind a `FieldRef`; anything else is refused, showing the form it takes.

    in      FieldRef("code"), "Match.prefix(a.code)"    →  out  "code"
    in      "code", "Match.prefix(a.code)"              →  ContractViolation: takes a field read
                                                           through the entity, as in
                                                           lambda a: Match.prefix(a.code)
    """
    if isinstance(value, FieldRef):
        return value.name
    raise ContractViolation(
        f"a presentation takes a field read through the entity, as in lambda a: {form}; "
        f"it was given {value!r}"
    )


class MatchMode(StrEnum):
    """How a literal is compared with one field."""

    EQUAL = "equal"
    PREFIX = "prefix"
    CONTAINS = "contains"


OPERATOR_OF_MODE: dict[MatchMode, Operator] = {
    MatchMode.EQUAL: Operator.EQ,
    MatchMode.PREFIX: Operator.STARTS_WITH,
    MatchMode.CONTAINS: Operator.LIKE,
}


class Match(DataTransferObject):
    """One field a literal is compared with, and how. What `Meta.search` publishes.

    Match.equal(a.code)        the code is the text
    Match.prefix(a.code)       the code starts with the text: 1.2.3 finds 1.2.3.01
    Match.contains(a.name)     the name contains the text, case-insensitive
    """

    field: str
    mode: MatchMode

    @classmethod
    def equal(cls, field: object) -> "Match":
        return cls(field=field_name(field, "Match.equal(a.code)"), mode=MatchMode.EQUAL)

    @classmethod
    def prefix(cls, field: object) -> "Match":
        return cls(field=field_name(field, "Match.prefix(a.code)"), mode=MatchMode.PREFIX)

    @classmethod
    def contains(cls, field: object) -> "Match":
        return cls(field=field_name(field, "Match.contains(a.name)"), mode=MatchMode.CONTAINS)

    def condition(self, text: str) -> Condition:
        """This match asked with a literal.

        in      Match(code, PREFIX), "1.2.3"    →  out  Condition(code, STARTS_WITH, "1.2.3")
        """
        return Condition(field=self.field, value=text, operator=OPERATOR_OF_MODE[self.mode])


@dataclass(frozen=True)
class Descending:
    """A field a list is ordered by, newest or largest first: `Descending(a.posted_at)`."""

    field: object


@dataclass(frozen=True)
class Expand:
    """A relation a detail brings whole: every scalar of each related record.

    `limit` pages a to-many relation. Without it the relation keeps the nested default.
    """

    field: object
    limit: int | None = None


@dataclass(frozen=True)
class Reference:
    """A relation a detail brings as its identity and display field: what a many2one shows."""

    field: object
    limit: int | None = None


@dataclass(frozen=True)
class Is:
    """One condition over a field, the triple a Criteria carries.

    Is(i.state, Operator.NE, InvoiceState.DRAFT)     →   ["state", "!=", "draft"]
    """

    field: object
    operator: Operator
    value: object


@dataclass(frozen=True, init=False)
class AllHold:
    """Every condition holds: `AllHold(Is(i.state, ...), Is(i.payment, ...))`."""

    conditions: tuple[object, ...]

    def __init__(self, *conditions: object) -> None:
        object.__setattr__(self, "conditions", conditions)


@dataclass(frozen=True, init=False)
class AnyHolds:
    """At least one condition holds: `AnyHolds(Is(i.kind, ...), Is(i.kind, ...))`."""

    conditions: tuple[object, ...]

    def __init__(self, *conditions: object) -> None:
        object.__setattr__(self, "conditions", conditions)


@dataclass(frozen=True, init=False)
class When:
    """The fields a hint covers while a condition holds.

    When(Is(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount)
    """

    condition: object
    fields: tuple[object, ...]

    def __init__(self, condition: object, *fields: object) -> None:
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True)
class Presentation[T]:
    """What the entity declares about how it is read. Every part is optional.

    `display` is the field shown beside the identity. `search` is how a literal finds a record
    (containment on the display when not declared). `order` is the list order, a bare field
    ascending and `Descending(...)` the other way. `detail` is what `get` brings when a caller
    asks for the detail: bare fields, `Expand(...)` and `Reference(...)`.

    The form hints, all defaults a client may override and none of them checked:
    `readonly` and `required` always; `readonly_when`, `required_when` and `visible_when`
    while a condition holds over the record (`When(...)`). Left alone, the fields the framework
    writes (`id`, `version`, …) are read-only and a field with no default that cannot be
    `None` is required.
    """

    display: Callable[[T], object] | None = None
    search: Callable[[T], tuple[Match, ...]] | None = None
    order: Callable[[T], tuple[object, ...]] | None = None
    detail: Callable[[T], tuple[object, ...]] | None = None
    readonly: Callable[[T], tuple[object, ...]] | None = None
    required: Callable[[T], tuple[object, ...]] | None = None
    readonly_when: Callable[[T], tuple[When, ...]] | None = None
    required_when: Callable[[T], tuple[When, ...]] | None = None
    visible_when: Callable[[T], tuple[When, ...]] | None = None
