# PRD_33: The client contract — each operation says what it is, exported raw with every wire's bindings

- **Status**: phase 1 (§1, §2, §4) built on `feat/client-contract-roles`, 2026-11-05, not
  committed: `role_of` beside `entities_of`; in `metamodel_of` the `context`, operations keyed by
  identity, `role` / `query` / `targets` / `input` / `output`, an edit's `values` as its model,
  the shared `$defs` named by their class, subscribers of domain events left out, a model's
  `search` and `detail` with only what they say, keys typed `EntityID` / `StrictEntityID` /
  `Reference` published as `relation`; `x-sincpro-role` on OpenAPI (FastAPI and
  Starlette) and OpenRPC, `role` in gRPC's `Describe`, MCP's `metamodel://` by being
  `metamodel_of`. Phases 2 and 3 not built.
- **Found by**: forge's console on `@sincpro/web-framework`. Every route and every Command is
  declared by hand (`web/src/domains/project/adapters/forge_project.tsx`), every wire type is
  written by hand (225 lines in `web/src/domains/*/domain/*.ts`), and they drift: `revokeShare`
  is still built by hand although the web package fills any `{key}` of a route from the body.
- **Depends on**: `@entity_tags` (#232), PRD_27 (the entity's default reads: `Get`, `GetMany`,
  `LiteralSearch`, `Search`), `PreviewEntity`, `Edit` (`EntityEdits`), PRD_14 (declared exposure:
  bindings, `gateway.manifest()`), the metamodel (`metamodel_of`, `export_metamodel`,
  `docs/spec/model.md` §10), PRD_06 (runtime use cases change the model's version).
- **Enables**: `@sincpro/web-framework` PRD_01 (domain and client generated from this contract),
  #214 (TypeScript types), #208 (contract tests between services), the protocol-agnostic half of
  #230 (1).
- **Philosophy**: PRD_16's — one derivation, two outputs, never two sources. The bus already
  knows what each operation is: the DTO's type says it for every framework read and edit, and
  `@entity_tags` says which entity every other Command concerns. This PRD **publishes** that; it
  never asks a project to declare it a second time. The framework exports JSON. It never writes a
  client: each language's package generates its own from this file.

## Problem

1. **No operation says what it is for its entity.** The metamodel's operations
   (`introspection/metamodel.py:66`) carry `entities`, `reaches`, `dry_run` and `description`; REST
   adds `x-sincpro-dto`, `x-sincpro-layer`, `x-sincpro-entities`, `x-sincpro-dry-run`
   (`entrypoints/adapters/fastapi/wire.py:502`). None says «this is `Project`'s search, this its
   get, this its edit». A client that builds itself from the contract has to be told by hand.
2. **No operation carries its input and output in the metamodel.** `operations_of` knows them
   (`OperationDescription.input`, `.response`); `metamodel_of` drops them.
3. **What an operation is and how it is reached are never exported together.** The first is the
   metamodel (protocol-agnostic); the second is each wire's binding (`gateway.manifest()`). The
   OpenAPI document mixes both, for REST only, and in forge weighs 44 MB because criteria's schema
   is copied into each of its 19 GETs (forge `docs/26-arquitectura-sobre-el-framework.md` §1).
4. **gRPC cannot fill the gap.** The exported `.proto` is `google.protobuf.Struct` on every
   method (`entrypoints/adapters/grpc/proto.py`), so `protoc` generates clients with no types until
   PRD_16 is built.

## Background

| What exists | Where | What it gives this PRD |
|---|---|---|
| `named_in_type(dto)` | `ddd/reads/reads.py:237` | the entity a framework DTO names: `Get[Issue, R]` → `Issue` |
| `@entity_tags` | `introspection/entity_tags.py` | the entities any other Command concerns |
| `operations_of(bus)` | `introspection/operations.py` | per Command: input model, response, description, entities |
| `metamodel_of` / `export_metamodel` | `introspection/metamodel.py` | every model's contract, the operations, a version; same code, same bytes |
| `gateway.manifest()` | `entrypoints/services/gateway.py:852`, `ManifestEntry` | per wire: the Command's identity, its name there, kind, method, binding, `deprecated`, `sunset`, `replacement` |
| One binding per wire | `entrypoints/domain/bindings.py` | `RestBinding`, `RpcBinding`, `GrpcBinding`, `McpBinding`, `QueueBinding` |

**Industry.** Smithy keeps the service model protocol-agnostic and attaches each protocol as a
trait (the `http` trait maps an operation to a route); a resource names its lifecycle operations
(create, read, update, list). Kubernetes publishes an aggregated discovery document (KEP-3352,
stable in 1.30) and one OpenAPI v3 document per group-version, content-addressed
(`/openapi/v3/apis/<group>/<version>?hash=<hash>`). Dataverse generates early-bound classes
(`pac modelbuilder`) and keeps late-bound access for what no generated class covers.

## The specification

**MUST**, **MUST NOT**, **SHOULD**, **MAY** as in RFC 2119.

### 1. The role of an operation

- Each operation in `metamodel_of` **MUST** carry `role`, one of `get`, `get_many`,
  `literal_search`, `search`, `events`, `preview`, `edit`, `command` — `events` is
  `DomainEvents[T, R]`, a record's history (PRD_28).
- The role **MUST** be derived from the DTO: the framework read or edit class it descends from
  (`issubclass`), so a project's own subclass keeps it — forge's
  `TenantSearch[T, R](Search[T, R])` is a `search`. A DTO that descends from none is a `command`.
- The framework **MUST NOT** ask for a new declaration. A project that already uses the
  framework's DTOs and `@entity_tags` gets every role without changing a line.
- A `command` with no entity (no tag, no DTO naming one) belongs to its context:
  `entities: []`. It is published like any other.
- Each operation **MUST** carry `query`: it writes nothing, so a retry or a cache is safe — every
  role that reads (`get`, `get_many`, `literal_search`, `search`, `events`, `preview`) and a
  `command` whose DTO is a `Query`.
- Each operation **MUST** carry `targets`: per entity it belongs to, the field of its Command
  that names that entity's record — the first rule that names one field: a field typed as its
  key (`EntityID[E]`), the name `<entity>_id` (unless typed as another's key), and — with one
  entity only — its one bare `EntityID`, else `id`. A rule that finds two names none. An entity
  with no such field is absent: the operation runs on no record of it. A client fills the
  field with the record's key; it never guesses one that is absent. A Command that keeps
  `str` keys answers what the names alone say.
- A key typed `EntityID[E]` **MUST** count `E` among the entities its operation concerns when
  `E` is an entity of the context — no `@entity_tags` needed; another context's (`Reference`)
  is said beside the field and is no entity of this one.
- A use case that reacts to a domain event is no operation a client calls: it **MUST NOT** be
  in `operations`.
- The metamodel **MUST** say its `context` and key each operation by its Command's identity, as
  the export does (§3) and `gateway.manifest()` lists each binding.

### 2. Its shapes

- Each operation **MUST** carry `input` and `output` as JSON Schema 2020-12, with `$ref` into the
  export's models where they reach one.
- criteria's schema **MUST** appear once, under `$defs`, and every operation that takes a
  criteria **MUST** reference it.
- A definition under `$defs` **MUST** be named by its class alone: its name, or — two classes
  sharing it — each by its module (`shop__orders__Line`). Two classes of one name and one shape
  are one definition. No name **MAY** come to mean another class because an operation was added:
  a generated type keeps its shape, or is gone and the client's build says so.
- An edit's and a preview's `values` — the record the form holds — **MUST** be its entity's
  model (`{"$ref": "#/models/<Entity>"}`).
- A model's `search` and `detail` (`x-sincpro`) **MUST** be written with only what each criteria
  says: what a blank criteria says left out at every depth, an expression whole.
- A key **MUST** say whose it is by its type — `EntityID[Issue]`, `EntityID[Partner, int]`,
  `EntityID[Device, UUID]`, `StrictEntityID[Issue]` —, published as what it holds with
  `x-sincpro.relation`, in an
  operation's input and on a model's field (`contract_of`): the entity's name for one of this
  context, `<context>.<Entity>` for another's, named once in `shared/` (`class
  Tenant(Reference, context="auth")`). `relation` is the meta's word for the aggregate on the
  other side; the field's `type` says it holds a key, not the record, and no filter crosses it.
  Each is read and written by pydantic's own rule for what it holds: an `EntityID` takes what
  its plain type takes (a text key, a `uuid.UUID` as its 32 hex); `EntityID[E, UUID]` is
  pydantic's `UUID` — any spelling in, the standard's 36 out, `format: uuid`; a
  `StrictEntityID` is pydantic's `UUID7`, held and sent as the framework's 32 hex and published
  `^[0-9a-f]{12}7[0-9a-f]{3}[89ab][0-9a-f]{15}$` — never `format: uuid`, which asks for dashes.
  A contract with no key typed so **MUST NOT** change.
- `dry_run` and `description` stay as they are. `deprecated`, `sunset` and `replacement` come
  from the exposure (`ManifestEntry`) and **MUST** be carried, so a generated method can say
  `@deprecated`.

### 3. The raw export

`export_client_contract(gateway, path)` (the name is an open question) writes one JSON file:

```json
{
  "format": "sincpro.client-contract/1",
  "version": "<sha256 of everything below>",
  "contexts": {
    "project": {
      "models": { "Project": { "…": "contract_of(Project)" } },
      "operations": {
        "project.CommandSetProjectTenant": {
          "role": "command",
          "entities": ["Project"],
          "input": { "$ref": "#/contexts/project/models/…" },
          "output": { "…": "…" },
          "dry_run": false,
          "description": "Set the project's home tenant; empty leaves it with none.",
          "deprecated": null
        }
      },
      "bindings": {
        "rest": {
          "project.CommandSetProjectTenant": {
            "method": "PUT",
            "path": "/projects/{id}/tenant",
            "path_fields": ["id"]
          }
        },
        "jsonrpc": { "project.CommandSetProjectTenant": { "method": "…" } },
        "grpc": { "project.CommandSetProjectTenant": { "method": "/…" } },
        "mcp": { "project.CommandSetProjectTenant": { "tool": "…" } }
      }
    }
  },
  "$defs": { "Criteria": { "…": "…" } }
}
```

- Operations **MUST** be keyed by the Command's identity (`registered_name`,
  `billing.CommandIssueInvoice`): the same key in every wire's bindings.
- The export **MUST** be deterministic, like `export_metamodel`: keys sorted, indented, a final
  newline. The same code writes the same bytes, and a diff shows only what changed.
- It **MUST** hold only what the gateway exposes. An `@internal` operation is not in it.
- A REST path's `{name}` is a field of the DTO (the wire's rule today); `path_fields` lists them,
  so a client fills them from the body.
- A command line **SHOULD** write it without serving anything (for a build or a CI step), e.g.
  `python -m sincpro_framework.introspection export-client-contract app.http:gateway out.json`.
  Each project wraps it in a `make contract` target.
- A running service **SHOULD** serve the same document (`GET <base>/client-contract.json`) with
  its `version` as the `ETag` (#200), so a client compares its snapshot with the live one. It
  **MAY** split it per context, content-addressed, as Kubernetes does.

### 4. The same facts on each wire

Where a wire already describes its operations, it **MUST** add the role:

- OpenAPI: `x-sincpro-role` on each operation, next to `x-sincpro-entities`.
- OpenRPC: the same extension on each method.
- MCP: `metamodel://<context>` answers `metamodel_of`, so it carries the role and the shapes.
- gRPC: `Describe` carries the role.

### 5. Models that change at runtime

- A use case loaded at runtime (PRD_06) or an installed extension already changes the model's
  version. The export and the live document **MUST** answer the new version; a read keeps
  answering `model_version`.
- **The export is a snapshot; the live document is the truth.** Nothing here makes a client fail
  against a newer server: a client built on an older export keeps working, and tells its
  developer it is behind.

### 6. Conformance — what the release MUST prove

- A bus with `Get`, `GetMany`, `LiteralSearch`, `Search`, `PreviewEntity` and `Edit` of one
  entity, a project subclass of `Search`, two tagged Commands and one untagged: every role right,
  the untagged one a `command` with `entities: []`; every `query` and every `targets` right; a
  subscriber of a domain event absent.
- Two classes named alike: neither keeps the bare name, each operation points at its own shape;
  adding an operation never points an existing name at another shape.
- Two exports of the same code are the same bytes.
- Changing a route changes only `bindings`. Adding a field changes only `models` and `version`.
- An `@internal` operation is absent.
- `@sincpro/criteria`'s `metaFromContract` still reads every model of the export:
  `contract-parity.json` is unchanged, because models are not touched.

### 7. Release phases

| Phase | What | Release |
|---|---|---|
| 1 | `role`, `input`, `output` in `metamodel_of`; `x-sincpro-role` on each wire (§1, §2, §4) | minor |
| 2 | `export_client_contract` and its command line (§3) | minor |
| 3 | The live document with its `ETag`, split per context (§3, after #200) | minor |

## Evidence

- forge: routes, Commands and wire types written by hand in its console; its backend already
  uses `Get`, `Search`, `Edit` and `@entity_tags` on nearly every operation (the `project`
  context: every Command tagged, every read a framework DTO or a subclass of one).
- `@sincpro/web-framework` `docs/ROADMAP.md`: «la meta no dice qué acciones tiene una entidad».
- forge `docs/26-arquitectura-sobre-el-framework.md` §1: a 44 MB discovery document.

## Decisions

- **D1 — The role is derived, never declared.** The DTO's type and `@entity_tags` already say
  it; a second declaration would drift from the first.
- **D2 — What is agnostic, how is per wire, one file joined by identity** (Smithy's split). A
  client changes wire by changing its transport, never by regenerating its domain.
- **D3 — JSON only.** The framework writes no TypeScript and no client. A generator belongs to
  its language's package (TypeScript: `@sincpro/web-framework` PRD_01).
- **D4 — `input`/`output` type a client whatever its wire.** gRPC keeps `Struct` until PRD_16;
  a typed gRPC client is typed by this export, not by the `.proto`.
- **D5 — What every client would otherwise guess is published, derived.** `query`, `targets`
  and an edit's typed `values` come from what is already declared (the DTO's type, the field
  names, `@entity_tags`): no new directive, and no generator in any language carries the rule.
- **D6 — A definition is named by its class, in one pass over every schema** (pydantic's own
  naming): a name depends on no other operation, so a generated type never changes shape under
  its name.
- **D8 — A key is typed, open by default.** Whose record a key names is the one thing no
  declaration said; `EntityID[E]` says it with the type a field already has, takes what a
  `str` (or `int`) takes so typing it changes no caller, and `StrictEntityID` is the opt-in
  rule. In the editor it is the value it holds (an alias for the type checker, a class
  pydantic reads at runtime), so no call site wraps a value; whose it is is checked when its
  schema is built. `Entity.id` stays `str`: projects keep keys of their own.
- **D9 — The standard's rules, the framework's text.** What a key takes and how it is written
  is pydantic's (`UUID`, `UUID7`): no reading of our own to keep in step. The framework's id
  stays 32 hex, not RFC 9562's 36: every id stored, drafted, recorded and held by a client is
  written so, and changing it changes them all. A key that wants the standard's own text is
  `EntityID[E, UUID]`. Outside a model a key is read with pydantic's `TypeAdapter` — a
  constructor of its own would be a second reading, and a type checker cannot call an alias.
- **D7 — The metamodel says what an operation is; a wire, how it is reached.** `query` is true
  for a `Get` that is no `Query`, while REST keeps serving it as `POST` and OpenRPC's
  `x-idempotent` stays false: making the framework's reads `Query` would move every route and turn
  on query caching for them — a decision of its own.

## Open questions

- **Two operations of one role for one entity** (forge: a second search of projects for ⌘K):
  which is the entity's own? Proposal: none, unless the project declares it; a client then has
  to name one.
- An entity's contract does not carry a strict key's pattern (a field's description holds no
  pattern): an operation's input does.
- **The framework's reads as `Query`**: `Get`, `GetMany`, `LiteralSearch` and `PreviewEntity`
  are no `Query`, so the wires serve them as writes (D7).
- The name of the export, of the command line and of the format identifier.
- Out of scope, unchanged: who may call an operation (each wire keeps publishing what it does
  today), layout, and what #230 asks beyond (1).
