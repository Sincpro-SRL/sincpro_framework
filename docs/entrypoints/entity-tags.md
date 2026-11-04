# Entity tags

The entities a use case concerns, declared once and read by everything that describes the bus:
the tags of every wire, the catalog, a screen that lists what can be done with a record, an
agent asking what it may do with an issue.

```python
from sincpro_framework.introspection import entity_tags

@forge.feature(CommandMoveIssue)
@entity_tags(Issue, Project)
@rpc()
class MoveIssue(Feature): ...
```

## What it is

A category, nothing more. The bus dispatches by the Command as it always did; nothing runs,
routes, guards or refuses because of a tag, and a use case that declares none belongs to its
context alone — a month's close, a dashboard.

A use case may concern several entities: moving an issue is reached from the issue and from the
project, a wizard sits between two of them. The order means nothing and an entity named twice
counts once.

A framework DTO that names its entity in its type concerns it without saying so: `Get`,
`GetMany`, `LiteralSearch`, `Search`, `DomainEvents`, `PreviewEntity` and `Edit` —
`QueryGetIssue(Get[Issue, ResponseIssue])` concerns `Issue`.

## How it is read

| Reader | What it gets |
|---|---|
| `operations_of(bus)["CommandMoveIssue"].entities` | `(Issue, Project)`; `to_json()` gives `"entities": ["Issue", "Project"]` |
| `features(bus)` / `app_services(bus)` | `FeatureOrAppServiceMetadata.entities` |
| OpenAPI (`FastApiGateway`, `RestGateway`) | each operation's `tags` end with `Issue`, `Project` — a generated client groups the method under both |
| OpenRPC (`rpc.discover`) | `{"name": "Issue"}`, `{"name": "Project"}` beside the context's tag |
| MCP | the tool's tags hold `Issue`, `Project` beside its layer |

A wire never derives a route or a name from a tag: with several entities a path would be a
guess, so REST paths stay what the binding declares.

## The rules

- On the Feature, the ApplicationService or the Command — both are read, together.
- Each entity is its class, so a rename reaches it and a typo fails where it is written:
  `@entity_tags("Issue")` and `@entity_tags()` are refused, showing the form.
- Stored apart from the class, as an exposure decorator stores its binding: the class is
  returned as it is, a subclass inherits nothing by accident, the order of the decorators never
  matters.

## Why a tag and not an owner

An aggregate owns its transaction, but a screen, a wire and an agent group by what a use case is
about, and that is often more than one entity. A tag says exactly that and asks nothing of how
the use case is written. It lives in `introspection` — the description of the bus every wire
reads — and not in the core, which never reads it.
