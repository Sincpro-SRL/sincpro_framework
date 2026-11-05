# The metamodel: each entity described once, published as one contract

An entity is described **once**. The framework compiles what it declares into one model, every
reader takes its part from that model — persistence, previews and saves, the description a client
gets —, and every wire publishes it as one contract: JSON Schema 2020-12 with the framework's
vocabulary under `x-sincpro`. A client reads it once; every answer says only which version of the
model it is.

```
  declare              compile (at boot)        publish                    consume
  @dataclass Entity    model_of(Sale)           discovery document         a UI, a TS client, an agent
  @compute / @check    affects, judged_by       components.schemas.Sale    read once, revalidated by
  presentation         formulas, fingerprint    = JSON Schema + x-sincpro  its version
  ValueObject(holds=)  + what the store knows
                                   a read: {"sales": [...], "model_version": "3f9c…"}
```

The specification — requirements, decisions, measurements — is [spec/model.md](../spec/model.md).

## What the entity declares

Nothing new is registered anywhere: the model is read off what the class already says.

| Declared | Becomes, in the contract |
|---|---|
| the annotations, a value object | the property's JSON Schema: `type`, `format`, `enum`, `anyOf […, null]`; an exact number as `{"type": "string", "format": "decimal"}` |
| `ValueObject(Decimal, name="Discount", holds=lambda v: 0 <= v <= 30)` | `minimum` / `maximum` / `enum` where JSON Schema says it, the expression whole under `x-sincpro.holds` |
| `@compute(lambda s: s.total, depends=lambda s: s.lines)` | `affects` on each field it depends on, `readonly` on the computed one, and `computes` — a formula — when its body is one `return` of `+`, `-`, `*` over the record's own fields, or a `sum(… for one in self.lines)` of such |
| `@check(lambda s: [s.state, s.lines])` | `judged_by` on each field it watches |
| `presentation = Presentation["Sale"](readonly_when=lambda s: When(s.state != State.DRAFT, s.note), widgets=…)` | the hints and their conditions as expressions; `widget`, the kind's own unless the presentation names another |
| `field(metadata={"label": {...}})`, an enum's translations | `title`, `x-sincpro.label`, `choice_labels` |
| a relation the store keeps | `relation`, `many`, `navigable`, its keys, a `$ref` to the related entity's contract |

What the store knows that the class does not — a column's nullability, how a relation is kept, the
fields an extension adds — is told to the metamodel when the store describes the entity: the SQL
mapper at boot (`map_aggregates`, which also fails there on a declaration it cannot read), memory
on its first read.

## In Python

```python
from sincpro_framework.ddd.metamodel import contract_of, description_of, model_of

model_of(Sale).affects             # {"lines": ("total",)}
model_of(Sale).judged_by           # {"state": ("delivery_when_confirmed",), …}
model_of(Sale).formulas            # {"total": {"op": "sum", "of": "lines", …}}
model_of(Sale).fingerprint         # the version: equal wherever the same code runs
contract_of(Sale)                  # what a client is published, related entities under $defs
```

The metamodel of a whole bounded context — every model its operations reach, the operations a
client calls on each, one version — and the file a build writes from it, for a client that works
offline or generates its types and its client:

```python
from sincpro_framework.introspection import export_metamodel, metamodel_of

metamodel_of(billing)                                   # {"bounded_context", "version", "models", "operations", "$defs"}
metamodel_of(billing, {"QueryGetInvoice"})              # what those operations reach, named by class
export_metamodel(billing, "web/src/metamodel.json")     # the same code writes the same bytes
```

Each operation is keyed by its Command's identity — `registered_name`, `<context>.<Class>`, the
key `gateway.manifest()` lists every wire's binding by — and says what it is for its entity and
what it takes and answers, protocol-agnostic; how it is reached is each wire's binding (PRD_33):

```python
metamodel_of(billing)["operations"]["billing.QueryGetInvoice"]
# {"role": "get", "query": True, "entities": ["Invoice"], "targets": {"Invoice": "id"},
#  "reaches": ["Invoice", "Partner"], "dry_run": False, "description": "…",
#  "input":  {"properties": {"id": {…}, "criteria": {"$ref": "#/$defs/Criteria", …}}, …},
#  "output": {"title": "ResponseInvoice", "properties": {"invoice": {"$ref": "#/models/Invoice"}, …}}}

metamodel_of(billing)["operations"]["billing.CommandVoidInvoice"]["targets"]
# {"Invoice": "invoice_id"}
```

- **`role`** is read off the Command's type, never declared (`role_of`): the framework DTO it
  descends from — `Get` → `get`, `GetMany` → `get_many`, `LiteralSearch` → `literal_search`,
  `Search` → `search`, `DomainEvents` → `events`, `PreviewEntity` → `preview`, `Edit` → `edit` —,
  so a project's own base over one (forge's `TenantSearch(Search)`) keeps it; anything else is a
  `command`, with the entities `@entity_tags` names, or none.
- **`query`**: it writes nothing, so a retry or a cache is safe — every role that reads, and a
  `command` whose DTO is a `Query`. How a wire reaches it is the wire's: REST still serves a
  `Get` that is no `Query` as `POST`.
- **`targets`**: the field that names the record — or the records — of each entity it belongs
  to. Per entity, the first rule that names one field: a field typed as its key
  (`EntityID[Invoice]`, `list[EntityID[Invoice]]`), then the name `<entity>_id` or `<entity>_ids`
  (unless typed as another's key), then — with one entity only — its one bare key, else `id` or
  `ids`. A rule that finds two names none: nothing is guessed. A client fills
  the field with the record's key to run the operation on that record; an entity with no such
  field is no record it runs on — one it creates, a page of it, the context's own.
- **`models`** holds each entity's contract and every shape a record holds inside — a value
  object, a line —, named by its class (`ProjectSnapshot`, `OrderLine`) and written once, so a
  generated type has its name. Every pointer between them points at `models`.
- **`input` / `output`** are JSON Schema 2020-12. An entity is a pointer into `models`; an
  edit's and a preview's `values` — the record the form holds — is its entity's model. Anything
  else nested — criteria's schema above all — is written once under the metamodel's `$defs`,
  named by its class: its name, or, when two classes share it, each by its module
  (`shop__orders__Line`, `shop__stock__Line`); two classes of one name and one shape are one. A
  name never comes to mean another class because an operation was added: a generated type keeps
  its shape, or is gone. `output` is what `execute` declares, else the response the DTO names
  (`Get[Invoice, ResponseInvoice]`); `{"type": "null"}` for an answer of nothing, `null` when
  nothing is declared.
- A use case that reacts to a domain event is no operation a client calls: the bus runs it, the
  metamodel leaves it out.

### A key that says whose it is

A field holding another record's key says whose it is by its type — never by its name:

```python
from uuid import UUID

from sincpro_framework.ddd import EntityID, EntityKey, Reference

class Tenant(Reference, bounded_context="auth"):   # shared/: auth's tenant, never imported
    """auth's tenant."""

class CommandMoveIssue(DataTransferObject):
    issue: EntityID[Issue]                       # the framework's id: a UUID v7, 32 hex
    destination: EntityID[Project]               # in a field of any name
    tenant: EntityID[Tenant]                     # another context's: "auth.Tenant"
    owner: EntityKey[User]                       # a context's own key: an email, a slug
    partner: EntityKey[Partner, int]             # a whole number — an Odoo record
    device: EntityKey[Device, UUID]              # a standard UUID, as pydantic reads and writes one

@dataclass(kw_only=True)
class Issue(Entity):
    project_id: EntityID[Project] | None = None
```

- **`EntityID` is the framework's id — always a UUID v7; `EntityKey` a context's own key.**
  Each is read and written by pydantic's own rules:

  | Annotation | Holds | Takes | Writes |
  |---|---|---|---|
  | `EntityID[E]` | `str` | a UUID v7 in any spelling (pydantic's `UUID7`) | 32 lowercase hex, its pattern |
  | `EntityKey[E]` | `str` | any text; a `uuid.UUID` as its 32 hex | the text |
  | `EntityKey[E, int]` | `int` | what an `int` field takes | the number |
  | `EntityKey[E, UUID]` | `uuid.UUID` | any spelling of a UUID (pydantic's `UUID`) | the standard's 36, `format: uuid` |

  An `EntityID` is what `new_entity_id` mints: 32 hex and not the standard's 36, because every
  id already stored, drafted, recorded and held by a client is written so; a dashed or capital
  spelling, or a `uuid.UUID`, is read into it, anything else refused as pydantic refuses it
  (`uuid_version`, `uuid_parsing`). An `EntityKey` rewrites no text: a slug may hold dashes.
  Outside a model a key is read as pydantic reads any type:
  `TypeAdapter(EntityID[Issue]).validate_python(value)`.
- **In the editor** it is the value it holds — any `str` is an `EntityID[Issue]`, an
  `EntityKey[Partner, int]` is an `int`, an `EntityKey[Device, UUID]` a `UUID` —, so nothing is
  wrapped at a call site. A type checker cannot bound an alias's parameter, so whose a key is
  is checked when its schema is built: an `EntityID[int]` fails at import, naming what it got.
- **Published** as what it holds, whose it is under `x-sincpro.relation` — the word a relation
  already says the aggregate on the other side with; the field's `type` (`text`, `integer`,
  `uuid`) says it holds a key, never the record, and no filter crosses it. An entity of this
  context is named alone (`"Project"`, a key of `models`), another context's with its context
  (`"auth.Tenant"`, from its `Reference`); an `EntityID` adds the pattern of a UUID v7 to the
  operation's input — never `format: uuid`, which asks for dashes.

```json
{"type": "string", "pattern": "^[0-9a-f]{12}7[0-9a-f]{3}[89ab][0-9a-f]{15}$", "x-sincpro": {"relation": "Project"}}
{"type": "string", "x-sincpro": {"relation": "User"}}
{"type": "integer", "x-sincpro": {"relation": "Partner"}}
{"type": "string", "format": "uuid", "x-sincpro": {"relation": "Device"}}
```

- **Several keys** — `list[EntityID[MoveLine]]`, the lines a reconciliation matches — are a key
  too: a `text[]` field naming `MoveLine`, the field `targets` names for it. One use case may
  concern N entities — reconciling move lines, a move, payments —, each its own field.
- **Another context's entity**: import its class and the key is published as this context's
  (`relation: "Tenant"`, its model in this contract) — the class does not know its context. To
  say it is another's, or when no class exists here to import (another service, Odoo, an external
  system), name it once in `shared/` with a `Reference`: `class Tenant(Reference,
  bounded_context="auth")` → `relation: "auth.Tenant"`, no model, no entity of this bounded
  context. A project of many bounded contexts names them once — a `StrEnum` of its own,
  `BoundedContext` — and passes the member to both `UseFramework(BoundedContext.AUTH)` and
  `Reference(bounded_context=BoundedContext.AUTH)`: no bounded context's name is written twice
  as a literal, and none is ever mistaken for the runtime context a use case reads
  (`use_context`).
- **Read**: the field an operation runs on (`targets`), and the entities it concerns — a key of
  an entity of this context says it, with no `@entity_tags`; another context's does not.
- An entity's own identity stays `id: str` — a project keeps its own keys (an email, a slug).

How a model is read — its `search` and its `detail` under `x-sincpro` — is published with only
what each criteria says (`rendered_criteria`): what a blank criteria says is left out at every
depth, a field brought as it is is `{}`, and an expression is written whole. Whoever reads it
fills the defaults back.

## Where it is published

No route of its own: each wire's discovery document carries it, and a model appears only when an
operation that wire exposes reads or answers it.

| Wire | Where |
|---|---|
| REST (`FastApiGateway`, `RestGateway`) | `/openapi.json`, `components.schemas.<Entity>`; each operation says `x-sincpro-role`, `x-sincpro-entities`, `x-sincpro-dry-run`, and `x-sincpro-query` when it only reads; a JSON URL parameter points at its definitions, written once |
| JSON-RPC | the OpenRPC document (`rpc.discover`, `/openrpc.json`); each method says `x-sincpro-role`, and `x-idempotent` on a read |
| gRPC | `sincpro.Introspection/Describe`: each method says its `role` |
| MCP | the resource `metamodel://<context>`: `metamodel_of` of what the tools expose — roles, targets and shapes included; a read's tool is read-only |

A discovery document is tagged by its content (`ETag`): a client that holds it is answered `304`.

### The client contract: what an operation is, and how each wire reaches it

The document a client generates its domain and its client from joins the metamodel to how each
wire reaches every operation, by the Command's identity — one file per bounded context, named by
the alias the gateways publish it under:

```python
from sincpro_framework.entrypoints import client_contracts, export_client_contract

rest = FastApiGateway({"project": project_bus})        # the bus is named "acme-project"
export_client_contract([rest, rpc, grpc, mcp], "web/src/contracts")
# {"project": Path("web/src/contracts/project.contract.json")}
```

```json
{
  "format": "sincpro.client-contract/1", "bounded_context": "acme-project", "alias": "project",
  "version": "9b1c…",
  "models": {"…": "…"}, "operations": {"tracker.CommandMoveIssue": {"…": "…",
    "deprecated": false, "sunset": null, "replacement": null}}, "$defs": {"…": "…"},
  "bindings": {
    "rest": {"tracker.CommandMoveIssue": {"method": "PUT",
             "path": "/tracker/issues/{issue_id}/move", "path_fields": ["issue_id"]}},
    "rpc":  {"tracker.CommandMoveIssue": {"method": "tracker.move_issue"}},
    "grpc": {"tracker.CommandMoveIssue": {"method": "/tracker.v1.TrackerService/MoveIssue"}},
    "mcp":  {"tracker.CommandMoveIssue": {"tool": "move_issue"}}}
}
```

- **`bounded_context` and `alias`**: `bounded_context` is the bounded context's own name, what
  every identity starts with (`acme-project.CommandMoveIssue`) — never the runtime context a use
  case reads —; `alias` is the name the gateways publish it
  under — what a client names the context by (`project:issue`), what the file and the live route
  are named after. An alias that names two bounded contexts across the gateways is refused.
- **Only what the gateways expose**: an `@internal` use case is in no contract, a wire that does
  not publish an operation has no binding for it. How a wire is reached is the wire's own word
  (`Wire.reached_by`): REST the verb, the full path and `path_fields` — the Command's fields its
  `{name}`s take, filled from the body —; JSON-RPC and gRPC the `method`; MCP the `tool`.
- **Deprecation**: an operation every wire deprecates alike says it (`deprecated`, `sunset`,
  `replacement`); a binding deprecated on its own wire says so in the binding.
- **The same code, the same bytes**: keys sorted, indented, a final newline; `version` is the
  fingerprint of the whole file, and each model keeps its own (`model_version`).
- **Without serving anything**, for a build or a CI step:

  ```make
  contract:
      poetry run python -m sincpro_framework.entrypoints export-client-contract \
          app.http:gateways web/src/contracts
  ```

- **Live**: every HTTP gateway (FastAPI, JSON-RPC) serves `GET /client-contract/<alias>.json`
  — the bytes the export writes for it, built when asked, so a use case registered or a model
  changed while the service runs is the next answer —, its `version` the `ETag`, `304` to a
  client that holds it, out of the OpenAPI document. `app(contract_path=None)` serves none.

Every read answers the version of its model (`model_version`) — `ResponsePaginatedQuery`,
`ResponseRecord`, `ResponseRecords` and an `Edit` alike — and never the model again: another
version means read the document again.

## How a client consumes it

**`@sincpro/web-framework`** — nothing in the application changes:

```ts
const transport = restTransport({ base: "/v1", headers: () => ({ Authorization: `Bearer ${token}` }) });
// `document`: "/openapi.json" by default, at the origin of `base`; null reads none
```

The transport reads the document on the first read, finds the operation of each route and builds
the definition lists, forms and selects use; an answer of another version reads it again. A form
evaluates the hints and what a value holds itself, computes a published formula itself, and asks
the server for a preview only when a change reaches a computation with no formula or a rule.

**`@sincpro/criteria`**, without the web framework:

```ts
const document = await (await fetch("/openapi.json")).json();
const schemas = document.components.schemas;
const meta = metaFromContract(schemas.Sale, schemas);      // relations described too
const sales = resource<Sale>(restSource("/v1/sales", { meta }));
const page = await sales.page(criteria<Sale>().where(["state", "=", "posted"]));
page.version === versionOf(schemas.Sale);                  // else: read the document again
valueOf(meta.fields.total.computes!, record);              // exactly as the server computes it
```

**An agent** reads `metamodel://<context>` before it writes a criteria or a command: each field,
the operators it takes, how relations meet, what a change reaches.

## When a model changes

A use case loaded at runtime or an extension installed changes what is published: what an
extension overlays is part of the declaration, so the model compiled again has another
fingerprint, and the log says which model changed and both versions. The next read answers the
new version; a client reads the document again.

## Lines are named by their identity

A record held in a list — a sale's lines — that has an identity is named by it in every path: an
error's pointer (`#/values/lines/0192…/qty`), a warning's path, what a form says changed
(`lines.0192….qty`), the per-line states of a preview. A line that is a plain value, with none, is
named by its position. A form mints the identity of a line it creates (UUID v7) and the server
builds the line under it.

## Why

A description built per answer cost a client the same kilobytes on every page, was built twice
(memory and SQL disagreed on a label), and could not be validated by anything but our own code.
Described once, published as JSON Schema, a model is checked by any validator in any language, its
version is a fingerprint every replica agrees on, and a client asks the server only what it cannot
answer itself.
