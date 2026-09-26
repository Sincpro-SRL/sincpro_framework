# Introduction

## The problem

Every Sincpro service reads records for a screen, for another service and for a test, and every
one used to do it its own way: a repository method per question, a filter syntax per endpoint, a
different page shape per listing, and no way to bring a related record without a second call
that the client had to know about. When a context outgrew one database, or one record started to
live in another context, every Feature that read it changed.

This layer is one answer to all of that, and it is small: one way to ask, `Criteria`; one shape
back, `EntityCollection` with its definition, `Meta`; one door for writes, `Repository`; one way
to say what happened, `DomainEvent`. What a client can ask is what the answer says it can ask.

## The mental model, in one page

```
  client                       Feature                         database / other context
  ──────                       ───────                         ────────────────────────
  Criteria  ────────────────▶  repository.search(Aggregate, criteria)
    where · order · page          │ validated against Meta: what cannot be answered → dropped
    specification                 │ one statement for the page
    grouping · count · meta       │ one statement per named relation, for the whole page
                                  ▼
  EntityCollection  ◀──────────  items · count · cursor · dropped · meta
    records, typed                (records carry their resolved relations)
    meta says what to ask next
```

- **An aggregate is a plain dataclass.** Its annotations say the types, the cardinality of its
  relations and the shape of its embedded values. It imports nothing from persistence.
- **The table is declared once, beside the mapping.** `map_aggregates` ties class to table and
  reads the foreign keys; only what the tables cannot say is declared: a many-to-many, a relation
  that lives in another context or behind a function.
- **`Criteria` is the only thing that crosses a boundary.** It is JSON, it is typed, and it is
  *reflexive*: the answer carries `Meta`, which says what may be asked next.
- **A relation is brought once per page, never per row.** The client names it in the
  specification; inside a Feature it also resolves lazily on first touch.
- **Writes take the aggregate.** `save`, `remove`, a unit of work in `context()`, a version check
  that refuses a stale write. Never an update by criteria.

## A first example, end to end

The domain, plain Python:

```python
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity, EntityCollection

@dataclass
class Author(Entity):
    name: str
    books: list["Book"] = field(default_factory=list)      # one2many, from the foreign key

@dataclass
class Book(Entity):
    title: str
    pages: int
    author_id: str
    author: Author | None = None                            # many2one, same foreign key

class Books(EntityCollection[Book]): ...
```

The infrastructure, once, beside the tables:

```python
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates

shelf = registry()
author_table = entity_table("author", shelf.metadata, Column("name", Text, nullable=False))
book_table = entity_table(
    "book", shelf.metadata,
    Column("title", Text, nullable=False),
    Column("pages", Integer, nullable=False),
    Column("author_id", Text, ForeignKey("author.id"), nullable=False),
)
map_aggregates(shelf, {Author: author_table, Book: book_table})   # relations inferred

repository = Repository(Database("sqlite:///library.sqlite3"))    # injected as self.repository
```

The Feature, which never learns any of the above:

```python
class CommandListBooks(Query): ...                # carries a Criteria
class ResponseListBooks(ResponsePaginatedQuery):
    books: list[Book]

@catalog.feature(CommandListBooks)
class ListBooks(Feature):
    repository: Repository
    def execute(self, dto: CommandListBooks) -> ResponseListBooks:
        page = self.repository.search(Books, dto.criteria)
        return ResponseListBooks.of(page, dto.criteria)
```

The request, from anywhere:

```json
{"where": {"field": "pages", "operator": ">", "value": 200},
 "order": [{"field": "pages", "descending": true}],
 "pagination": {"limit": 20},
 "specification": {"title": {}, "author": {"specification": {"name": {}}}}}
```

The answer: twenty books of more than two hundred pages, longest first, each with `id` and
`title` and its author as `{"id", "name"}`, plus `count`, a `cursor` for the next page, an empty
`dropped`, and `model_meta_data` cut to the same three fields, saying which operators `pages`
takes and that `author` is a `many2one` identified by `author_id`. Two statements ran.

## Glossary

| Word | Meaning here |
|---|---|
| Aggregate | The dataclass a table is mapped to; the unit `save` takes |
| Criteria | What is being asked: filter, order, page, what to bring back, how to count |
| Specification | The part of a criteria that says what to bring back of each record, recursively |
| Meta, definition | What may be asked about an aggregate: its fields, their types, their operators, its relations |
| EntityCollection | A page of records with its cursor, count, dropped and definition |
| Relation | A field that points at another aggregate, brought by a resolver |
| Embedded | A value object stored inside the row, described, never resolved |
| Resolver | Anything called with keys and a criteria that answers related records |
| Unit of work | `repository.context()`: one session, one transaction, lazy relations |
