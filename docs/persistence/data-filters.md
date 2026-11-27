# Data filters — a rule every read applies and every write is checked against

A data filter is what EF Core calls a *global query filter*, ABP a *data filter* and Hibernate
`@Filter`: a condition the store adds to every read of the aggregates it applies to, read when
the statement runs, and checked against every record written. It knows nothing of tenants,
owners or states; tenancy is one filter, a row-level access rule another.

## What it is

```python
class OnlyPublished(DataFilter):
    name = "published"

    def criteria(self, aggregate):
        if "state" not in getattr(aggregate, "__annotations__", {}):
            return None                                  # not this aggregate
        return Criteria(selection=Condition(field="state", value="published"))

    def stamp(self, record):                             # optional: fill a new record
        ...
```

| Member | Meaning |
|---|---|
| `name` | what `DataFilters.disabled(name)` turns off |
| `criteria(aggregate)` | what every read of `aggregate` adds — `None` when it does not apply; read per statement, so a `FromContext` value or `use_context()` is the request's own |
| `stamp(record)` | fills what a new record leaves empty, before the write is checked |
| `guards_writes` | `True` (the default): a record saved or removed outside is refused — an isolation; `False`: a default view |

## How it is used

```python
database = Database(url, filters=[OnlyPublished()])
database.filters.add(another)                 # or later

memory = MemoryRepository(filters=[OnlyPublished()])   # the same rule in tests

with DataFilters.disabled("published"):       # this execution only
    repository.search(Articles)
```

Every door of the store reads through it:

- a repository's reads — `get`, `browse`, `search`, `count`, measures, groupings, exports;
- a Feature called inside another one's unit of work;
- a plain session: `unit.session.execute(select(Article))`, and ORM `update`/`delete`;
- the relations loaded from a filtered record.

Every flush stamps the new records and refuses one outside a filter that guards writes,
whichever door the write took. The fingerprint of a read — what a query cache keys by —
includes the filters with the request's values.

## Edges

What SQLAlchemy never sees as ORM is not filtered: `text()`, a Core statement on a `Table`,
`search_sql`. A filter naming a field the aggregate cannot answer is refused (`ProgrammingError`)
rather than dropped — a filter silently left out reads wide.

## Why on the store

A rule on a repository instance (`narrowed`) holds for whoever goes through that instance; a
filter on the store holds for whoever touches the database, which is why it sits beside the
stamping and the change tracking. `narrowed(scope)` remains: a narrowing of one view, by hand.
