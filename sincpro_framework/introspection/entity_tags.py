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

What a use case IS for its entity — its get, its search, its edit, or a command on it — is read
the same way, off the DTO's type, never declared (`role_of`):

    role_of(QueryGetIssue)          ->  Role.GET        # Get[Issue, …]
    role_of(QueryListProjects)      ->  Role.SEARCH     # TenantSearch(Search[…]): still a search
    role_of(CommandMoveIssue)       ->  Role.COMMAND    # descends from no framework DTO

Context: stored apart from the class, as an exposure decorator stores its binding, so the class is
returned as it is, a subclass inherits nothing by accident and the order the decorators are
stacked in never matters. Weakly held: a use case loaded again lets its old class go.
"""

from collections.abc import Callable
from enum import StrEnum
from weakref import WeakKeyDictionary

from sincpro_framework import exceptions
from sincpro_framework.ddd.editing.edits import Edit
from sincpro_framework.ddd.entity.entity import Entity
from sincpro_framework.ddd.entity.entity_id import key_of
from sincpro_framework.ddd.query import Query
from sincpro_framework.ddd.reads.reads import (
    DomainEvents,
    Get,
    GetMany,
    LiteralSearch,
    PreviewEntity,
    Search,
    named_in_type,
)

_declared: WeakKeyDictionary[type, tuple[type, ...]] = WeakKeyDictionary()
"""The entities each Feature, ApplicationService or Command declared, by class."""


class Role(StrEnum):
    """What an operation is for the entity it belongs to — what a client builds a screen from
    without being told a route: the list is its `search`, the record its `get`, the form its
    `edit` and `preview`, the history its `events`, the buttons its `command`s."""

    GET = "get"
    GET_MANY = "get_many"
    LITERAL_SEARCH = "literal_search"
    SEARCH = "search"
    EVENTS = "events"
    PREVIEW = "preview"
    EDIT = "edit"
    COMMAND = "command"


_ROLES: tuple[tuple[type, Role], ...] = (
    (Get, Role.GET),
    (GetMany, Role.GET_MANY),
    (LiteralSearch, Role.LITERAL_SEARCH),
    (Search, Role.SEARCH),
    (DomainEvents, Role.EVENTS),
    (PreviewEntity, Role.PREVIEW),
    (Edit, Role.EDIT),
)
"""Each framework DTO of one entity and the role it gives — none of them descends from
another, so the order never decides."""


def role_of(dto: type) -> Role:
    """What the operation answering `dto` is for its entity, read off the DTO's type.

        role_of(QueryGetIssue)          ->  Role.GET        # class QueryGetIssue(Get[Issue, R])
        role_of(QueryListProjects)      ->  Role.SEARCH     # TenantSearch(Search[T, R]) subclass
        role_of(CommandEditGroup)       ->  Role.EDIT       # Edit[Group, R]
        role_of(CommandMoveIssue)       ->  Role.COMMAND    # tagged or not: a command

    1. The framework DTO it descends from (`issubclass`), so a project's own base over one —
       a `Search` that narrows by tenant — keeps its role.
    Final: `command` for a DTO that descends from none: every write with an intent, every
    query of the context's own.
    """
    for framework, role in _ROLES:
        if isinstance(dto, type) and issubclass(dto, framework):
            return role
    return Role.COMMAND


def only_reads(dto: type) -> bool:
    """Whether the operation answering `dto` writes nothing — a retry, a cache, a `GET` and a
    read-only hint are safe: a `Query`, or a framework read of an entity, its own subclass too.

        only_reads(QueryGetIssue)       ->  True    # Get[Issue, …]: no `Query`, still a read
        only_reads(QueryPreviewIssue)   ->  True    # PreviewEntity: computes a form, stores nothing
        only_reads(CommandEditIssue)    ->  False   # Edit: stores a draft, or the record
        only_reads(CommandMoveIssue)    ->  False   # a command of the context's own

    Context: what every wire publishes as a read and the metamodel's `query` say — one rule.
    """
    return (isinstance(dto, type) and issubclass(dto, Query)) or role_of(dto) not in (
        Role.EDIT,
        Role.COMMAND,
    )


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
    declared, the entity a framework DTO names in its type, then each entity a field of its
    Command is the key of (`EntityID[Project]`) — each once, in that order.

        entities_of(MoveIssue, CommandMoveIssue)        ->  (Issue, Project)
        entities_of(IssueReads, QueryGetIssue)          ->  (Issue,)
        entities_of(Archive, CommandArchive)            ->  (Project,)  # project: EntityID[Project]
        entities_of(CloseMonth, CommandCloseMonth)      ->  ()     # the context's alone

    A `None` — a handler not found — concerns nothing. A key of another context's entity
    (`Reference`) is said beside the field, and is no entity of this one.
    """
    found: list[type] = []
    for one in classes:
        if one is None:
            continue
        found.extend(_declared.get(one, ()))
        named = named_in_type(one)
        if named is not None:
            found.append(named[0])
        for field in getattr(one, "model_fields", {}).values():
            key = key_of(field.annotation)
            if key is not None and key.entity is not None and issubclass(key.entity, Entity):
                found.append(key.entity)
    return tuple(dict.fromkeys(found))
