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

The metamodel of a whole bounded context — every model its operations reach, the operations that
belong to each, one version — and the file a build writes from it, for a client that works offline
or generates its types:

```python
from sincpro_framework.introspection import export_metamodel, metamodel_of

metamodel_of(billing)                                   # {"version", "models", "operations"}
metamodel_of(billing, {"QueryGetInvoice"})              # what those operations reach
export_metamodel(billing, "web/src/metamodel.json")     # the same code writes the same bytes
```

## Where it is published

No route of its own: each wire's discovery document carries it, and a model appears only when an
operation that wire exposes reads or answers it.

| Wire | Where |
|---|---|
| REST (`FastApiGateway`, `RestGateway`) | `/openapi.json`, `components.schemas.<Entity>`; each operation says `x-sincpro-entities` and `x-sincpro-dry-run` |
| JSON-RPC | the OpenRPC document (`rpc.discover`, `/openrpc.json`) |
| MCP | the resource `metamodel://<context>`: `metamodel_of` of what the tools expose |

A discovery document is tagged by its content (`ETag`): a client that holds it is answered `304`.
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
