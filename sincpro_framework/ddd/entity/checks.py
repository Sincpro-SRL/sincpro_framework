"""The rules an entity holds over its own fields, declared on the method that checks each one,
so a form is warned while it is edited and a save refuses what breaks them.

    @dataclass
    class Project(Entity):
        state: ProjectState = ProjectState.DRAFT
        deadline: date | None = None
        repositories: list[Repository] = field(default_factory=list)

        @check(lambda p: [p.deadline, p.state])
        def deadline_when_confirmed(self) -> None:
            if self.state == ProjectState.CONFIRMED and self.deadline is None:
                raise exceptions.InvalidValue("A confirmed project needs a deadline")

        @check(lambda p: p.repositories)
        def one_writable_repository(self) -> None:
            if not any(one.writable for one in self.repositories):
                raise exceptions.InvalidValue("At least one repository must be writable")

The method raises `InvalidValue` when the rule is broken; with no path it points at the first
field it watches — list first the one the user fixes — and a path of its own
(`("repositories", 2, "name")`) points into a line. The lambda names the fields the rule reads,
through the entity, and runs once when the class is described: a name that is not a field is
refused there.

Where it runs decides what a failure does — never the rule:

    a change in a form (`read_only()`)   the checks the change reaches   →  a `FormWarning`
    a save                               every check, the lines first    →  `ChecksFailed`

A record an aggregate owns — a line — declares its own checks; each runs on every line and
points at it (`("lines", 2, "price")`). A rule across lines belongs to the aggregate, which sees
them all. A rule that reads another aggregate, or anything outside the record, is not a check:
it is a hook (`before_save`), which has the bus.

The rows stored before a rule existed are found with `violations_of(repository, Project,
Project.deadline_when_confirmed)`. A rule a database must answer by itself — filtered, counted
or grouped over millions of rows — is also a `computed(sql=…, python=…)` field, which the check
reads.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CHECK = "__sincpro_check__"
"""The attribute under which a method carries its `CheckDeclaration`."""


@dataclass(frozen=True)
class CheckDeclaration:
    """What `@check` left on a method: the lambda as written, read when the class is described."""

    watches: Callable[[Any], object]


def check[F: Callable[..., Any]](watches: Callable[[Any], object]) -> Callable[[F], F]:
    """The method checks a rule over `watches`: `@check(lambda p: [p.deadline, p.state])`.

    `watches` names one field alone or a list of them — what a change has to reach for a form
    to be warned; a failure with no path of its own points at the first. The method raises
    `InvalidValue` when it is broken.
    """

    def declared(method: F) -> F:
        setattr(method, CHECK, CheckDeclaration(watches))
        return method

    return declared
