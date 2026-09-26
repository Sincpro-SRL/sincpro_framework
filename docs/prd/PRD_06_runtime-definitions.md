# PRD_06: Runtime definitions — change behaviour without a deploy

- **Status**: proposal
- **Depends on**: PRD_04 (extension points), PRD_07 (bus generations)
- **Research**: scratchpad `11_runtime_loading_reload.md`

## Problem

Odoo lets an administrator add a field, an automation, a scheduled action or a snippet of Python
from the screen, and the running server picks it up: the definitions are rows in the database, the
registry is rebuilt, the other workers are told through a Postgres sequence. An ERP on this
framework needs the same — a tenant configures, nobody deploys — and today everything is fixed
when the bus is built.

## Goals

1. Definitions that live **as data** — in a database or in files — and change what a bounded
   context does, without a deploy.
2. The code that interprets them is ordinary, shipped, tested code; a definition never becomes a
   new Python class in a running process.
3. A definition is validated when it is stored and when it is loaded; an invalid one never
   replaces a valid one.
4. Snippets are open: the consumer decides what code it runs.

## Non-goals

- Creating Features, DTOs or aggregates at runtime.
- Changing a code-defined table's columns at runtime: that is a migration (PRD_05).
- Sandboxing snippets: the consumer's responsibility.

## Prior art

| | How | What we take |
|---|---|---|
| Odoo | Views, actions, crons, server actions are rows read at use time; only model/field changes rebuild the registry; `base_registry_signaling` sequence tells other workers | Definitions as rows; a version the other replicas compare against |
| Frappe | DocType as data, `get_meta` cached per doctype and cleared on change; logic stays in shipped code | **The closest fit**: shape as data, behaviour as code |
| Salesforce | A metadata change is a validated, all-or-nothing deploy | Validate before swapping; keep the old one when it fails |
| `importlib.reload` | Old instances keep old classes; stale `from x import Y` | Why definitions are never reloaded as modules — every registry here is keyed by class |

## What can be defined at runtime

| Kind | Stored as | Interpreted by | Example |
|---|---|---|---|
| **Extra fields** | field declarations per aggregate | one JSON `extra` column on the table + a pydantic model built from the declarations + a `Meta` overlay | a tenant adds `warranty_months: int` to `Product` |
| **Rules** | `when` (Criteria grammar) + action | a `Rule` on the repository, rebuilt per generation | "refuse an invoice over 50.000 without an approver" |
| **Interceptors** | Command + `when` + action or snippet | one shipped interceptor (PRD_04) that reads the definitions | "before `CommandCreateInvoice`, run this check" |
| **Workflows** | the workflow JSON | the workflow runner | `bill_order` |
| **Schedules** | Cron + policies + Command name + input | the scheduler (PRD_08) | "every Monday 08:00, `CommandSendAgingReport`" |
| **Snippets** | source code | a `SnippetEngine` | the body of an action, a computed value |

An action is always one of two things: **a Command executed on the bus** (by name, with an input
mapping), or **a snippet**. Both leave the same trace a Feature leaves.

## Storage

```python
class DefinitionSource(Protocol):
    def load(self, context: str) -> Definitions: ...   # everything for one bounded context, one version
    def version(self, context: str) -> int: ...
```

| Source | For |
|---|---|
| `DatabaseDefinitions(database)` | tenant-edited definitions; one table, a version bumped in the same transaction as the change |
| `FileDefinitions(path)` | definitions shipped in the repository, reviewed in a PR |
| `LayeredDefinitions(files, database)` | files as the base, the database on top — Dynamics' layering: the top one wins, removing it reveals the one below |

`Definitions` is immutable: one version of everything a context has, validated as a whole.

## Validation — twice

**When stored** (the screen or the API that edits a definition): the shape, and every reference —
a Command the bus does not know, a field an aggregate does not have, a `when` over a field that
does not exist, a Cron without a timezone — refused with the definition and the reason.

**When loaded** (building a generation, PRD_07): the whole set again, against the bus it will run
on. A set that fails does not become a generation; the previous one keeps serving and the failure
is reported with the version that failed.

## Extra fields

```python
@dataclass
class Product(ExtensibleMixin, Entity):
    name: str = ""
    price: int = 0

product_table = entity_table("product", md, ..., *extra_columns())   # one JSON column

product.extra["warranty_months"] = 24          # validated against the declared extra fields
repository.search(Products, Criteria.model_validate(
    {"where": {"field": "extra.warranty_months", "operator": ">=", "value": 12}}
))
```

- Declared per aggregate (name, type, label, required, default), stored as definitions.
- Validated on `save` by a pydantic model built from the declarations of the current generation.
- Filterable and orderable through `Criteria` (`extra.<field>`) — the translator reads the JSON path.
- Described by `Meta`, as an overlay beside the class's own cached description, so a screen renders
  them like any other field.
- A field that needs an index or a foreign key is no longer an extra field: it is a migration.

## Snippets

```python
class SnippetEngine(Protocol):
    def compile(self, source: str, name: str) -> Snippet: ...

class Snippet(Protocol):
    def __call__(self, **values: Any) -> Any: ...
```

- `PythonSnippets` is the default: compiled once per generation into a module registered under a
  stable name (`sincpro_snippets.<context>.<name>.v<version>`), so tracebacks, `inspect` and the
  failure report's `error_at` point at the snippet and its version.
- Open by design: what a snippet can import and do is the consumer's choice. A consumer that wants
  isolation plugs another engine (CEL, WASM, a subprocess) without changing a definition.
- Every execution is a span with the snippet name and version; a failure is reported like a
  Feature's.

## Phases

1. `DefinitionSource`, `Definitions`, validation, rules and interceptors as data.
2. Extra fields: `ExtensibleMixin`, `extra_columns()`, `Meta` overlay, `Criteria` on `extra.*`.
3. `SnippetEngine` + `PythonSnippets`; workflows and schedules as definitions.
4. `LayeredDefinitions`.

## Open questions

- Are definitions per tenant (one set per database or per tenant column), per context, or both?
- Who may edit a definition, and is there a history / rollback of versions?
