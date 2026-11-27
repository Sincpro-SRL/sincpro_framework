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
| `refusal(record)` | what that refusal raises — `ProgrammingError` by default; tenancy answers *not found* |

## How it is used

Two places, each a decision of its own:

```python
# for a block of an execution — and everything it calls: what a use case decides
with DataFilters.applied(OnlyPublished()):
    repository.search(Articles)

# for every read of a store, whoever reads it: a rule of the data itself
database = Database(url, filters=[OnlyPublished()])
memory = MemoryRepository(filters=[OnlyPublished()])   # the same rule in tests

with DataFilters.disabled("published"):       # off for this block only
    repository.search(Articles)
```

What a block applies or turns off lives in the execution context: the nearest block decides,
a nested Feature, a hook, a thread handed the context and an async call inherit it, and nothing
of it leaves the process. Tenancy is a filter applied by the use cases that ask for it
([tenancy](../tenancy/README.md)).

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
`search_sql`. An ORM `insert(Model).values(...)` and the values of an `update(...).values(...)`
are not checked either — the rows an UPDATE reaches are filtered, what it sets is the code's. A filter naming a field the aggregate cannot answer is refused (`ProgrammingError`)
rather than dropped — a filter silently left out reads wide.

## Why both

A filter applied to a block is the use case's decision, in sight where it is made. A filter on
the store is the data's own rule, for whoever touches the database — beside the stamping and the
change tracking. `narrowed(scope)` remains: a narrowing of one repository view, by hand.
