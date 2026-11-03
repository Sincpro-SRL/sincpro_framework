"""The entities a use case concerns: a category whoever reads the bus groups it by — the tags of a
wire, the commands a screen offers next to a record, what an agent may do with an issue.

    @forge.feature(CommandMoveIssue)
    @entity_tags(Issue, Project)
    class MoveIssue(Feature): ...

    entities_of(MoveIssue, CommandMoveIssue)    ->  (Issue, Project)
    entities_of(IssueReads, QueryGetIssue)      ->  (Issue,)     # Get[Issue, …] says it

**A description, nothing more.** Nothing runs, routes, guards or refuses because of it: the bus
dispatches by the Command as it always did, and a use case that declares none belongs to its
context alone. A use case may concern several entities — a command reached from two screens, a
wizard between two of them — so the order means nothing and an entity named twice counts once.
A DTO that names its entity in its type concerns it without saying so — every framework DTO of
one entity does (`Get[Issue, ResponseIssue]`, `Search`, `Edit`, …), read by `named_in_type`.

Context: stored apart from the class, as an exposure decorator stores its binding, so the class is
returned as it is, a subclass inherits nothing by accident and the order the decorators are
stacked in never matters. Weakly held: a use case loaded again lets its old class go.
"""

from collections.abc import Callable
from weakref import WeakKeyDictionary

from sincpro_framework import exceptions
from sincpro_framework.ddd.reads.reads import named_in_type

_declared: WeakKeyDictionary[type, tuple[type, ...]] = WeakKeyDictionary()
"""The entities each Feature, ApplicationService or Command declared, by class."""


def entity_tags[T: type](*entities: type) -> Callable[[T], T]:
    """Tags the Feature, ApplicationService or Command it decorates with the entities it
    concerns, and returns the class as it is.

        @entity_tags(Issue, Project)    ->  MoveIssue, tagged Issue and Project
        @entity_tags()                  ->  ProgrammingError: names no entity
        @entity_tags("Issue")           ->  ProgrammingError: takes the entity's class

    1. At least one entity, each a class — so a rename reaches it and a typo fails here.
    Final: the decorator; tagging a class twice adds to what it had.
    """
    if not entities:
        raise exceptions.ProgrammingError(
            "entity_tags names the entities a use case concerns, as in "
            "@entity_tags(Issue, Project); it was given none"
        )
    wrong = [one for one in entities if not isinstance(one, type)]
    if wrong:
        raise exceptions.ProgrammingError(
            f"entity_tags takes each entity's class, as in @entity_tags(Issue); "
            f"it was given {', '.join(repr(one) for one in wrong)}"
        )

    def tagged(cls: T) -> T:
        """Stores the entities for `cls`, after any it was tagged with before."""
        _declared[cls] = tuple(dict.fromkeys((*_declared.get(cls, ()), *entities)))
        return cls

    return tagged


def entities_of(*classes: type | None) -> tuple[type, ...]:
    """The entities a use case concerns, read off its handler and its Command: what each one
    declared, then the entity a framework DTO names in its type — each once, in that order.

        entities_of(MoveIssue, CommandMoveIssue)        ->  (Issue, Project)
        entities_of(IssueReads, QueryGetIssue)          ->  (Issue,)
        entities_of(CloseMonth, CommandCloseMonth)      ->  ()     # the context's alone

    A `None` — a handler not found — concerns nothing.
    """
    found: list[type] = []
    for one in classes:
        if one is None:
            continue
        found.extend(_declared.get(one, ()))
        named = named_in_type(one)
        if named is not None:
            found.append(named[0])
    return tuple(dict.fromkeys(found))
