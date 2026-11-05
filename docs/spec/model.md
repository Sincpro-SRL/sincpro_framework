# Model and expressions specification

What an entity is, described once, and the language every reader of it speaks — the server, SQL,
memory, the browser and an agent. This page holds the requirements, the diagnosis of what exists,
the architecture it converges to, and the plan in steps that each ship as a minor release.
Updated in place with every change ([how a specification is kept](README.md)). Worked in
[#236](https://github.com/Sincpro-SRL/sincpro_framework/issues/236).

**Status:** ✅ built and held by a test · 🟡 partly · ⬜ not built — a proposal.

## Requirements

| # | Requirement | Status | Held by |
|---|---|---|---|
| R1 | An entity is described **once**, at runtime, into one immutable model every reader takes its part from — persistence, queries, forms, the published contract | ✅ | `tests/ddd/metamodel/test_model.py`, `test_layers.py::test_only_the_compiler_reads_what_an_entity_declares` |
| R2 | The model is grouped by concern — core, domain, query, presentation — so a reader sees at a glance what an entity has | 🟡 one model with the entity's declaration and what follows from it; its parts are not yet grouped by concern | `sincpro_framework/ddd/metamodel/domain/model.py` |
| R3 | A store adds only what it knows (column nullability, relation keys); it never builds the description itself | ✅ | `StoreFacts`; `tests/data_layer/orm/test_description_parity.py` |
| R4 | One expression language, JSON, with a schema, answered the same in Python, SQL and TypeScript | ✅ the predicate tree — `through` included —, its schema and the parity suite (`crossing` for relations) | `criteria.schema.json`, `tests/ddd/criteria/test_criteria_parity.py`, `parity.test.ts` |
| R5 | The query is a composition of named parts — selection, specification, order, pagination, grouping, count — each its own value, each validated against the model | ✅ each part its own value — `Expression`, `Specification`, `Sort`, `Pagination`, `Grouping`, `CountMode` — checked on its own (`Meta.accept`, `accept_specification`, `orderable`, the grouping's checks) | `tests/ddd/criteria/` |
| R6 | A filter crosses a relation (`Through`): some related record holds | ✅ | `tests/data_layer/orm/test_through.py` (memory and SQL) |
| R7 | A rule over one value is portable: the browser validates it offline with a standard evaluator | ✅ published as JSON Schema keywords and as `x-sincpro.holds`; the client checks it with `matches` | `tests/ddd/test_value_object_holds.py`, `@sincpro/criteria` `tests/contract.test.ts` |
| R8 | A rule across fields that a client must evaluate is an expression of the language; a rule only the server can judge is an `@check` | 🟡 `@check` and the `*_when` hints exist | `tests/ddd/entity/test_checks.py` |
| R9 | The model is published as one contract — JSON Schema with the framework's vocabulary — in the discovery document every wire already publishes — OpenAPI, OpenRPC, MCP — with a parity suite between Python and TypeScript | ✅ published in OpenAPI (REST and FastAPI wires), OpenRPC and MCP (`metamodel://<context>`), readable by any JSON Schema validator, `contract-parity.json` holding the TypeScript reader to it | `tests/ddd/metamodel/test_contract.py`, `@sincpro/criteria` `tests/contract.test.ts` |
| R10 | Everything a client can derive from the model is published derived, never declared twice: operators, widget, what a change affects, whether a warning blocks a save, which operations and actions belong to an entity | 🟡 all of it but what #230 asks that nothing declares yet: when an action applies, a reference to another context, a semantic format ([§5](#what-230-asks-that-nothing-declares-yet)) | `tests/ddd/metamodel/test_contract.py`, `tests/ddd/entity/test_checks.py` |
| R11 | A record with an identity is addressed by it everywhere — paths of errors and warnings, `changed`, per-line states; a position only for a value with none | ✅ | `tests/ddd/editing/test_preview.py::test_a_line_with_an_identity_is_named_by_it_everywhere`, `tests/data_layer/orm/test_checks_sql.py`, `test_preview_sql.py::test_a_line_the_form_created_is_saved_under_the_identity_the_form_minted` |
| R12 | Expressions are compiled once per shape and evaluated by the compiled program; the core stays on the standard library, a faster evaluator is an optional extra | ✅ the memory evaluator; a SQL statement is compiled by SQLAlchemy | `tests/ddd/criteria/test_programs.py` |
| R13 | Layout — groups, tabs, columns — is the client's | ✅ by decision | [§12](#12-out-of-scope) |
| R14 | Compiling, holding, versioning and publishing models is one component with its own layers, like the framework's others — the metamodel | ✅ compile, hold, forget, warm up at boot, learn what a store knows, publish (`contract_of`), the metamodel of a bus and its export (`metamodel_of`, `export_metamodel`) | `sincpro_framework/ddd/metamodel/`, `test_layers.py`, `test_contract.py`, `tests/introspection/test_metamodel.py` |
| R15 | A model changes at runtime — a use case loaded from a store, an extension installed — and every reader sees the new one, with a new version | ✅ a mapping or an extension that changes a class forgets its model (`forget_models`); what an extension overlays is part of the declaration, so the model compiled again has another fingerprint, said once in the log; a use case loaded changes the bus's operations and so its metamodel's version and every document's `ETag`. Carrying the change to a client over SSE waits for [#201](https://github.com/Sincpro-SRL/sincpro_framework/issues/201) | `test_metamodel.py::test_an_extension_that_overlays_a_field_changes_the_model_and_says_so` |
| R16 | A metamodel's version is its content's fingerprint: every replica answers the same version with no coordination, and a client revalidates with it | ✅ each contract carries its model's fingerprint (`x-sincpro.version`), every read answers it (`model_version`), and a discovery document is tagged by its content (`ETag`, `304`) | `test_contract.py`, `tests/data_layer/orm/test_query_shapes.py`, `test_entrypoint_rest.py::test_the_document_is_tagged_by_its_content_and_a_client_that_holds_it_gets_304` |
| R17 | The compiler takes declarations, not only classes: a model can be built from data — a field a tenant adds, the metamodel another context published | ⬜ decided (D7) | [§11](#11-trajectory-what-the-requests-say-comes-next) |

## 1. Diagnosis

Measured and read in the code on 2026-11-04.

**The expression language is the healthiest piece.** The predicate tree (`Condition`, `All`,
`Any_`, `Not`) with dynamic values (`Today`, `Now`, `FromContext`, `Param`, `FromField`) is JSON,
has a JSON Schema (`@sincpro/criteria/criteria.schema.json`), three evaluators — memory
(`ddd/criteria/services/evaluation.py`), SQL, and TypeScript (`@sincpro/criteria/engine`) — and a
parity suite whose cases Python writes and TypeScript must answer the same, SQL's corners
included. The form hints (`readonly_when`, `required_when`, `visible_when`) already travel in it and
the browser evaluates them offline. It is hidden: it lives inside `criteria`, named after queries.

**The entity's description is the weak piece.**

| Finding | Evidence |
|---|---|
| Read in pieces, by many readers | `annotations_of` (uncached, `get_type_hints` per call), `presentation_of`, `computations_of`, `checks_of`, `computed_fields_of`, `framework_fields`, `defaults_of`, `owned`, the extension overlays — used from 17 modules. Every new capability adds a reader and every consumer must learn it |
| `Meta` is built twice | `describe_class` (memory, value objects) and `ModelIntrospection.describe` (SQL) repeat the domain part. They had drifted — a field with no label was a `MultiLanguage` in one and a `dict` in the other; one wording function now serves both and `test_description_parity.py` holds them together. Memory still does not describe an embedded value object (held as a known gap, `xfail` strict) |
| A field's declared type and its value disagree | a translated field annotated `dict[str, str]` is read back as a `MultiLanguage`, and serializing the record warns (`test_field_meta_unified.py` under `-W error`) |
| One flat object for every consumer | `FieldMeta` carries what a query needs (`ops`, `sortable`, `aggregates`), what a form needs (`readonly_when`, `default`, `total`) and what persistence needs (`parent_field`, `related_field`) side by side |
| Value objects are opaque | a value object's rule is a Python callable; its schema is the primitive's; a `Literal` or a `list[str]` inside one reaches the client as `unknown` |
| No contract | `Meta` has no schema and no parity suite; OpenAPI answers `{}` for every read, so a client stitches the runtime `Meta` with OpenAPI's command schemas (`sincpro_web_framework/src/core/schema.ts`) |
| Types mix two questions | `FieldType` puts primitives (`text`, `decimal`, `date`) beside semantics (`many2one`); the operators a field takes depend on the first, the widget on the second |
| Lines addressed by position | errors, warnings, `changed` and per-line states point at `("lines", 2, "qty")`; an answer that arrives after a line was inserted lands on the wrong line, though the server already edits and adopts lines by identity |

**Extending is getting harder** because each of the eight ways an entity declares something —
fields and `field(metadata=…)`, `computed`, `@compute`, `@check`, the `DEFAULT_*` class methods,
`presentation`, `translations()`, mixins and extensions — has its own reader, and whoever adds one
has to find every consumer. The concepts are right; their implementation is coupled. The answer
is not fewer concepts and not more directives: it is one place that compiles them.

## 2. The expression language

A language of its own, below queries, forms and rules. Four layers, each a plain value:

| Layer | What it is | Today |
|---|---|---|
| **Value** | a literal, or a value read when the expression is answered: `Today`, `Now`, `FromContext`, `Param`, a reference to another field | ✅ `criteria/domain/values.py` |
| **Expression** | the tree: predicates (`Condition`, `All`, `Any`, `Not`, `Through`); later, values computed from others (arithmetic, a sum over a relation) | 🟡 predicates; `Through` ⬜; value expressions ⬜ |
| **Checked expression** | the tree resolved against a model: every field known, every operator one its type takes, what is unknown dropped and named (`Dropped`) | 🟡 done by `Meta` while reading a criteria |
| **Program** | the checked expression compiled for a target — a closure in memory, a SQL clause, a function in TypeScript — once per shape, then reused | 🟡 SQL compiles; memory walks the tree on every row |

The pipeline is the one mature portable languages use (Google's CEL: parse → check → program):
the tree is data, checking is against a declared model, and evaluation runs a program built once.

### `Through`

`{"through": "lines", "where": <Expression>}` — some related record holds. It sits beside `All`,
`Any` and `Not`:

1. The node in `expression.py`.
2. Checked: the relation exists and is navigable; the inner expression is checked against the
   related model, recursively; what is unknown is dropped with `Dropped`, as today.
3. In memory: some related record holds.
4. In SQL: a correlated `EXISTS`, through the key pair the relation declares; a many-to-many goes
   through its table in between; a many-to-one uses `EXISTS` too, so no row is duplicated.
5. The model publishes `navigable` per relation. A relation resolved by a bus or over HTTP is not
   navigable, and a filter through it is refused.
6. Inside the `EXISTS`, the same scope the relation applies when it reads, so a filter never looks
   at records the reading would not bring.
7. `limit: 0` with a specification confirmed with tests, and [decisions §18](../persistence/decisions.md#18-what-is-deliberately-still-open) updated.

## 3. The model, compiled once

One immutable `Model` per class, built by a **model compiler** the first time anything asks — and
eagerly when a bus starts, so a mistake fails at boot. It runs at runtime, with no build step and
no generated code; exporting it to a file is the same model written out.

```
                    the class: annotations, field(metadata=…), @compute, @check, computed,
                    DEFAULT_*, presentation, translations(), mixins, extensions
                                          │
                                   model compiler  ◄── store facts (a store adds what it knows)
                                          │
                                        Model
        ┌──────────────┬─────────────────┼──────────────────┬──────────────────┐
       core          domain            query           presentation        contract
  identity, fields,  computations    operators per     labels, hints,     JSON Schema +
  primitive + kind,  (ordered),      field, sortable,  display, search,   vocabulary,
  relations, keys,   checks, value   groupable,        order, widget,     OpenAPI, OpenRPC,
  owned / written,   constraints,    aggregates,       totals             MCP
  navigable          what affects    navigable
                     what
```

- **Every reader takes its part of the same model.** The repository, the criteria checker, the
  form engine, the extension machinery and the publisher stop reading the class.
- **A store contributes facts, not a description.** The SQL adapter answers what a column allows
  and how a relation is keyed; memory answers nothing. One function builds `Meta` for both.
- **What can be derived is derived here, once:** the operators from the primitive type; the widget
  from the semantic kind; what a field's change affects from the dependencies of `@compute` and
  the fields `@check` watches; whether a relation is written by the save or only pointed at.
- **The current readers become views of the model**, with the same names, so no consumer changes
  in the step that introduces it.

Types answer two questions, so a field carries both:

| | Answers | Values |
|---|---|---|
| **primitive** | what can be compared and how — the operators | `string`, `integer`, `number`, `decimal`, `boolean`, `date`, `datetime`, `list`, `object`, `reference` — JSON Schema's, with its formats |
| **kind** | what it means — the widget, the formatting | `money`, `email`, `url`, `percent`, `duration`, `enum`, `many2one`, `one2many`, `many2many`, the value object's name, a reference to another context (`auth:tenant`) |

## 4. The query, a composition of parts

A query is not one object with many fields; it is parts with names, each a value of its own,
composed:

| Part | Key a caller writes | Its concept |
|---|---|---|
| **Selection** — which records | `selection` (today `where`) | σ, an expression of §2 |
| **Specification** — which fields and which relations, at any depth | `specification` | π, projection |
| **Order** | `order` | τ |
| **Pagination** — how many, from where (cursor, offset) | `pagination` | a window over the result |
| **Grouping** — groups, measures, pivot | `grouping` | γ, aggregation |
| **Count** | `count` | — |

The keys stay the ones a caller already reads, except `where`, which becomes `selection`: it is
the one part whose name said a clause of SQL rather than what it does. The concepts of relational
algebra name what each part is in this page; they are not keys (D1).

`Criteria` stays the word a caller writes — the composition — and each part is checked against the
model on its own. The language underneath the selection is the expression language of §2; a
specification's nested parts are queries of the related model.

## 5. The published contract

The model is published as **one JSON Schema 2020-12 document per entity, with the framework's
vocabulary** (one `x-sincpro` object per schema and per property), inside the discovery document
each wire already serves — OpenAPI's components (a read answers a schema, not `{}`), OpenRPC's,
MCP's resources. How it reaches a client is [§16](#16-serving-the-models). A parity suite proves the
TypeScript reader understands what Python writes, as the expression language already does.

What a client is told, every item derived:

| Published | Derived from |
|---|---|
| primitive, kind, formats, constraints of a field | its annotation, its value object |
| operators, sortable, groupable, aggregates, navigable | the primitive, the relation |
| widget | the kind; `presentation(widgets=…)` overrides it with any name a client registers |
| what a change affects | `@compute` dependencies, `@check` watches |
| which operations belong to the entity, and its actions with when each applies ([#230](https://github.com/Sincpro-SRL/sincpro_framework/issues/230)) | `@entity_tags`, the Command, a condition in the expression language |
| a reference to another context ([#230](https://github.com/Sincpro-SRL/sincpro_framework/issues/230)) | the field's declared target |
| a warning's rule and whether it blocks the save | where the warning came from — an `@check` blocks, `warn_form` advises |

How it is written (`contract_of(Sale)`, `sincpro_framework/ddd/metamodel/services/contract.py`):

- **A schema per entity**: `type: object`, `title`, `properties`, `required` (the `required`
  hint), and the schema's own `x-sincpro` — `entity`, `identity`, `get_id`, `display`,
  `default_order`, `search`, `detail`, its `title` in every language and its `version`.
- **A property per field**: what JSON Schema says of its value — `type`, `format` (`date`,
  `date-time`, `uuid`), `enum` for choices, `anyOf […, null]` when it may be empty, the keywords of
  what its value object holds (`minimum`, `exclusiveMinimum`, `maximum`, `exclusiveMaximum`,
  `enum`), a shape inside the record inline, a relation as `$ref` to the related entity's schema —
  and its `x-sincpro`: `type`, `widget`, `label`, `help`, `readonly`, `default`, the `*_when`
  conditions as expressions, `total`, `sortable`, `groupable`, `aggregates`, `ops`, `computed`,
  `owner`, `choice_labels`, `exact`, the relation's `relation`, `many`, `identified_by`,
  `parent_field`, `related_field` and `navigable`, `holds`, `affects`, `judged_by`. What says nothing
  is left out.
- **An exact number travels as text** (`{"type": "string", "format": "decimal"}`): no numeric
  keyword is written on it — a validator would skip `minimum` on a string, silently —; what it
  holds travels whole under `x-sincpro.holds`.
- **What the store knows is said**: a store that describes an entity tells the metamodel what it
  knows of it (`StoreFacts`) — the SQL mapper at boot, memory on its first read —, and the contract
  says it: a many-to-many, a navigable relation, a column's nullability. An entity no store
  described yet is published as its annotations say, its relations not navigable.
- **Where it travels**: every response schema the catalog builds describes each entity it
  reaches by its contract — OpenAPI's components (REST and FastAPI wires) and OpenRPC's —; an
  operation says the entities it belongs to (`x-sincpro-entities`) and whether its Command may be
  run dry (`x-sincpro-dry-run`). MCP publishes the resource `metamodel://<context>` —
  `metamodel_of` of what its tools expose —, read before an agent writes a criteria or a command.

### What #230 asks that nothing declares yet

Three items of [#230](https://github.com/Sincpro-SRL/sincpro_framework/issues/230) are not derivable
from anything an entity or a Command declares today, so they wait for a decision rather than a
directive invented here:

| Asked | Why it is not derived |
|---|---|
| when an action applies to a record (`enabled_when`) | no declaration says it; the closest — an `@check` — refuses a save, it does not gate a Command |
| a reference to another context (`tenant_id` points at `auth:tenant`) | a field holding an identity says nothing of whose; a relation with a resolver names a class, not a context |
| a semantic format — email, url, percentage, duration | money is derived — a `Money` or a `MultiCurrency` field is shown as `money`, an exact number as `decimal` —; the others are a value object's name, which is not a contract |

## 6. Validation in every language

Three kinds of rule, three homes, no new directive:

| Rule | Home | Portable through |
|---|---|---|
| **One value** — a minimum, a set of choices | the value object, as an expression of what it holds (`holds=lambda v: v >= 0`, [D4](#how-a-value-object-says-what-it-holds-d4)) | **JSON Schema keywords** (`minimum`, `maximum`, `enum`) where one says the same, the expression whole beside them — evaluated offline by any client |
| **Across fields, evaluable by a client** — required when confirmed, a date after another | the expression language, already used by the `*_when` hints | the expression's own evaluators and parity suite |
| **Only the server can judge** — across lines, against stored data, against another service | `@check` (the record) or a hook (with the bus) | not portable, by nature; it answers on save and in a preview |

A value object keeps its Python rule for what no expression can say; what an expression can say
it declares as one, and the browser checks it offline.

## 7. Identity everywhere

A record with an identity is named by it in every path the framework writes or reads:
`("lines", "0192…", "qty")`, `changed=["lines.0192….qty"]`, per-line states keyed by identity,
the pointer `#/values/lines/0192…/qty`. A value with no identity — a line that is a plain value — is
named by its position. The client mints identities (UUID v7) for what it creates and the server
builds the record under the one it names (`apply_values`); `changed` may still name a line with an
identity by its position, and a path that names no record is refused. A preview answers a list of
records whose lines a computation moved, each with its identity.

## 8. Performance and compilation

Measured on 200,000 records, one expression of five nodes, CPython 3.12:

| Evaluator | Per record |
|---|---|
| walking the tree on every record, with the language's full comparison (`compare`) | 1.14 µs |
| compiled once (`program_of`): closures, each comparison decided once (`comparator`) | 0.63 µs — ×1.8 |
| the first prototype, comparing without the language's NULL and empty-list rules | 0.54 µs — the ceiling, not the language |

The compiled program is the step that matters and needs nothing beyond the standard library.
Beyond it, in order, only when a measurement asks:

| Option | Verdict |
|---|---|
| Compile to closures, cache by the expression's text | **built** — `ddd/criteria/services/programs.py`, held by `tests/ddd/criteria/test_programs.py`; same source, no dependency |
| mypyc: compile the same typed Python modules into a C extension, shipped as an optional wheel | kept for later — one source, as black does |
| Cython | rejected — a second language and a second source to keep in step |
| Rust through PyO3 | rejected for now — a toolchain for a gain pydantic-core already gives where it matters, validation |

The model compiler costs once per class; describing an entity stops calling `get_type_hints` on
every read.

## 9. What mature systems do, and what is taken

| System | Taken | Left |
|---|---|---|
| CEL (Google) | parse → check → program; one language, evaluators in every runtime, typed against a declared schema | its own syntax: ours is JSON, which every client already speaks |
| JSON Schema / OpenAPI 3.1 | value constraints and the published shape, with a vocabulary of our own | describing behaviour in it: expressions stay ours |
| Django `Model._meta` | the model compiled once per class, the one place every reader asks | its global registry: a model belongs to its class and its bus |
| SQLAlchemy Core, Ibis | one expression tree, compiled per target | — |
| Odoo `fields_get`, domains, `onchange` spec | a client told everything about a field; which fields trigger a recompute | prefix-notation domains; views and layout in the server |
| GraphQL introspection | the schema as data any client loads | a query language of its own |
| Pydantic v2 | describe once, compile a validator, reuse it | — |

## 10. Infrastructure: the metamodel

Compiling a model, holding it, versioning it and publishing it are not the domain's concerns: they
are the non-functional part — caching, concurrency, change at runtime, versions, export. They live
in one component with the same layers as the framework's others (`context`, `data_layer/caching`):
**`metamodel`** — the model of the models, as JPA and EF Core name it (D6):

| Layer | Holds |
|---|---|
| `domain` | `Model` and its parts (fields, relations, computations, checks, constraints, hints); `Metamodel` — every model a bus reaches, with their operations, actions and version; `StoreFacts`, what a store answers about a model |
| `services` | the model compiler; the checker of expressions against a model; the program compilers per target (memory, SQL); the renderer of the contract; the metamodel of a bus |
| `adapters` | what a store answers (`StoreFacts` from SQLAlchemy's mapper); the writer of the TypeScript parity fixtures |
| `infrastructure` | the registry of compiled models (one per class, built once under a lock, immutable); the bounded cache of programs by fingerprint; fingerprints; the warm-up a bus runs when it starts |
| `entrypoint` | what a project and the gateways call: the model of an entity (`model_of`), the metamodel of a bus (`metamodel_of`) — functions over the bus, as `operations_of(bus)` is, never a method added to the bus |

The language itself — values, expressions, the query's parts — stays in `ddd`: it is vocabulary,
with no infrastructure behind it. The metamodel compiles and serves it.

### Processes

| Process | When | What it does |
|---|---|---|
| **Compile** | the first read of a class; every mapped entity when a bus starts | builds the model from the class's declarations and the store's facts; a mistake fails at boot, naming the class |
| **Change** | a use case loaded at runtime (`runtime_use_cases`), an extension installed | compiles the models it touches again; the metamodel's version changes; the log says which model changed and both versions — and a client hears it over SSE once [#201](https://github.com/Sincpro-SRL/sincpro_framework/issues/201) is built; until then a read answering another `model_version` tells it |
| **Publish** | a client asks for a discovery document | renders the contract; answers its version as an `ETag` ([#200](https://github.com/Sincpro-SRL/sincpro_framework/issues/200)), so an unchanged catalog costs a `304` |
| **Export** | a build, by hand | writes a bus's metamodel to a file (`export_metamodel(bus, path)`, the dict `metamodel_of(bus)`: models, operations, version; same code, same bytes) — an offline bundle for the web and mobile clients, the source of TypeScript types ([#214](https://github.com/Sincpro-SRL/sincpro_framework/issues/214)), the parity fixtures |
| **Run** | a filter, a preview, a save | takes the program of an expression from the cache, compiled once per expression, model and target |

### What it holds to

- **Deterministic.** The same code renders the same contract, byte for byte, so the version is a
  fingerprint of the content and every replica agrees without sharing a store. A store is needed
  only for what is not code — a use case loaded at runtime keeps its source where it already does.
- **Immutable.** A compiled model never changes; a change compiles a new one and swaps it.
- **Safe across threads.** One compilation per class at a time; readers never see half a model.
- **Bounded.** Programs are cached by fingerprint with a size limit; models, one per class.
- **Observable.** Compile time, cache hits and refusals are metrics and log lines, as every other
  component's.
- **Standard library only.** The SQL facts adapter is part of the `[sqlalchemy]` extra.

## 11. Trajectory: what the requests say comes next

The requests of the last months, grouped by where they push, and what each needs from this plan.

| Trend | Requests that show it | What comes next, predictably | What in this plan carries it |
|---|---|---|---|
| **The client builds itself from the backend** | drafts and `Edit` (#229, #228), preview and computations, `@entity_tags` (#232), actions, references and formats (#230), TypeScript types (#214), the shared web and mobile client | configurable lists and dashboards, saved filters, mobile working offline and syncing | the contract in each discovery document, portable expressions and value rules, identity everywhere |
| **One screen, several contexts** | composing a screen from several contexts (#212), references across contexts (#230), the remote bus, contract tests between services (#208) | a federated metamodel: a BFF merging the metamodels of the contexts it calls | metamodels as data with references `context.Entity`; R17, a model built from a published metamodel |
| **Agents operate the system** | MCP on every bus, the problem shape with next steps (#183), entity tags in MCP | agents that write filters and fill forms | the contract as MCP resources; checked expressions that answer what they dropped and why |
| **Extend without forking** | extensions of entities owned by others, the extensible ERP plan, use cases loaded at runtime | fields a tenant adds without a deploy | the compiler over declarations (R17), changes at runtime (R15), the JSON slot already built |
| **Data at scale** | analytics from the model, `DataFrame`, pivots, `violations_of`, `Through` | reports across relations, alerts over rows | compiled programs, the query's parts, `Through` |
| **Live and long-running** | SSE (#201), long-running operations (#188), a response as a stream (#203) | live queries: a selection matched against each domain event as it happens | compiled programs make matching every event cheap; the metamodel's change event |
| **Language and messages** | the language of a call (#190), translatable messages (#187), titles of problems by language | every text a client shows in the caller's language | `MultiLanguage` in every part of the contract; a rule's message by key |

Two conclusions shape the design. **The model has to be data**, not only something read off a
class: three of the trends — federated metamodels, fields added by a tenant, models changing at
runtime — build a model from something that is not a Python class. And **the program, not the
tree, is what runs**: live queries and agents writing filters multiply the evaluations, so
compiling once is what keeps them cheap.

## 12. Out of scope

- **Layout** — groups, tabs, columns. The client's; a screen composes from the model.
- **Permissions of the viewer** — the application decides; the bus enforces ([#230](https://github.com/Sincpro-SRL/sincpro_framework/issues/230)).
- **Generated code** in Python — the model is runtime data; types for TypeScript come from the
  published contract ([#214](https://github.com/Sincpro-SRL/sincpro_framework/issues/214)).

## 13. Plan

Each step is a minor release, green on its own, with the consumers' tests run against it. A rename
of something public is listed in its step; nothing keeps an alias.

| Step | What | Public change |
|---|---|---|
| 0 ✅ | This specification; the description of memory and of SQL compared field by field (`tests/data_layer/orm/test_description_parity.py`); one wording function for both, so a label is never a bare `dict` | none |
| 1 ✅ | The metamodel (§10) and its model compiler: one `Model` per class, built over declarations (D7); the current `*_of` readers become its views; a store contributes facts; warm-up at boot; memory describes embedded value objects; a field's declared type agrees with the value it holds | none |
| 2 ✅ | The expression language as its own layer in `ddd` (§2): values, expressions, checking against a model, programs compiled and cached by the metamodel, held by the parity suite; `Through`; value objects declare what they hold as an expression (§6) | the language's package and names (D1) |
| 3 ✅ | The query as a composition (§4): selection, specification, order, pagination, grouping, count, each its own value and checked on its own | `where` becomes `selection` (D1) |
| 4 ✅ | The published contract (§5): JSON Schema with the vocabulary (`contract_of`), read schemas in the discovery documents, the metamodel's version, everything derived, what #230 asks that is derivable delivered through it; the description leaves the wire (D3) | a read answers `model_version`, never `entity_meta_data`; `Criteria.meta` is gone; `FormWarning.rule` and `blocks_save`, `FieldError.rule`, `FieldMeta.widget`, `affects`, `judged_by` |
| 5 ✅ | Identity everywhere (§7) | paths of errors, warnings and `changed` for records with identity; `FieldState.items` keyed by identity; a line with an `id` the record does not hold is built under it; `apply_values` loses `adopt` |
| 6 ✅ | Change at runtime and export (§10): recompile on a loaded use case or an installed extension, the change said, the export of a metamodel to a file | additive: `metamodel_of`, `export_metamodel`; an overlay is part of the model's fingerprint |
| 7 ✅ | The consumers: `@sincpro/criteria` reads the contract (`metaFromContract`, `versionOf`, a relation described by what it points at) and evaluates and checks `Through`, both held by parity files the framework writes; `@sincpro/web-framework` reads a bounded context's discovery document once (`restTransport`, `document`), revalidates it by `ETag` when an answer says another `model_version`, finds a line's states, errors and warnings by its identity, asks for a preview only when a change reaches something (`needsPreview`), shows a warning that blocks the save as an error; forge moves to the new names. The FastAPI wire publishes the contracts too. Not done: synthesis — pinned to framework 3.12, its port to the current framework is its own task; a widget registry and many-to-many editing in the web client, which need the client's own design | in the consumers |
| 8 ✅ | Value expressions: a computation simple enough to be written down also runs in the browser — derived from the `@compute` method's body (one `return` of `+`, `-`, `*` over the record's own fields and literal numbers, or a sum over the records a field holds), published as `x-sincpro.computes`, answered exactly by `valueOf` in `@sincpro/criteria` and computed by the web client before it asks for a preview; held by `formulas` in `contract-parity.json`. A division, a condition or a call has no formula: decimals divide by a context the languages do not share | additive: `Formulas`, `FieldMeta.computes`, `Model.formulas` |

## 14. Decisions

| # | Question | Decided |
|---|---|---|
| D1 | Rename the query's parts and the language | **The language is named for what it is (expression); of the query's keys only `where` becomes `selection`.** `specification`, `order`, `pagination`, `grouping` and `count` stay: they read well and every client already writes them. Relational algebra (projection, window, aggregation) names the concepts in the docs, never the keys |
| D2 | The contract's format | **JSON Schema 2020-12 with one `x-sincpro` object per schema and per property**, by the evidence: OpenAPI 3.1 is that same dialect and the framework already publishes it; MCP describes tools in it; the web client already reads it (`schema.ts`); a value's constraints are then checked by any validator in any language; TypeScript types are generated from it with existing tools. A JSON of our own would need a reader of our own in every client and nothing would validate it. The vocabulary has its own schema, as the expression language does |
| D3 | `Meta` | **It goes from the wire.** The contract (published) replaces it there and no old contract is kept: a read answers the model's version. Inside the process `Meta` stays what a store answers of a model — what a filter, an order and a specification are checked against and what a record is cut by —, built by the metamodel from the model and the store's facts. The entities and the ORM are used in-house, by projects not in production — forge, synthesis and the web client — which move with step 7 |
| D4 | How a value object says what it holds | **An expression, written with operators on a value stand-in**, beside the Python rule it already takes: what an expression can say is portable, what it cannot stays in Python. See below; the exact spelling is confirmed in step 2 |
| D5 | An accelerated evaluator | **Not now, and never required.** A program is compiled per target behind one contract, so a faster evaluator — mypyc, or any other — is one more target added later, in an extra, if a measurement asks for it |
| D6 | The component's name | **`metamodel`** |
| D7 | The compiler over declarations (R17): a model built from data, not only from a class | **Yes, from step 1**: the compiler's input is a declaration; reading a class is one way to get it, the only one until a trend asks for another |
| D8 | How a person writes an expression, typed, with the IDE's help | **A plain Python lambda, compiled from its syntax tree** (option D of [§15](#15-writing-an-expression-typed)): the only option that catches a misspelled field and a value of the wrong type with the checker alone, keeps `and`, `or`, `not`, `in`, `is None` and `any(… for … in …)` as Python writes them, and runs as Python too. It is the shape C#'s LINQ gives with expression trees and SQLAlchemy's `lambda_stmt` and Pony ORM use in Python; the program, not the lambda, answers, so there is one semantics. A program that builds an expression it does not know in advance uses the typed reference (C) or the JSON |

### How a value object says what it holds (D4)

Built (`ddd/value_object.py`, `tests/ddd/test_value_object_holds.py`), written as a lambda over the
value (D8):

```python
Amount = ValueObject(Decimal, name="Amount", holds=lambda v: v >= 0)
Percent = ValueObject(Decimal, name="Percent", holds=lambda v: 0 <= v <= 100)
Size = ValueObject(str, name="Size", holds=lambda v: v in ["S", "M", "L"])
Email = ValueObject(str, check_email, name="Email")       # a Python rule: the server judges it
```

The lambda's parameter is the value itself; its syntax tree is read once into an expression over
the field `value` (§15), never run. The server checks it on every construction, after the Python
rule, which may normalise the value; the schema publishes it — as `minimum`, `maximum`,
`exclusiveMinimum`, `exclusiveMaximum` or `enum` where a keyword says the same, and whole under
`x-sincpro.holds` — and every field typed with the value object carries it as `FieldMeta.holds`, so
a client checks it offline. A value object with only a Python rule works as it did.

## 15. Writing an expression, typed

Four ways a person can write `state = confirmed and no delivery`, each checked with pyright on
2026-11-04 against the same mistakes — a misspelled field, a value of the wrong type, a list of the
wrong type in `in`, a mistake inside a line — and run.

| | Written as | Field misspelled | Value of the wrong type | `and` / `or` / `not` / `in` / `is None` | Runs as Python too | Cost to build |
|---|---|---|---|---|---|---|
| **A. Functions** | `all_(eq(s.state, State.CONFIRMED), is_null(s.delivery))` | caught | **missed** — the checker widens to a union | as functions | no | low |
| **B. Operators on the stand-in** | `s.state == "confirmed"` | caught | missed | **`and` silently keeps the last part; `in`, `is` cannot be overloaded** | no | low — and wrong: the checker sees a `bool` |
| **C. A typed reference** | `(ref(s.state) == State.CONFIRMED) & ref(s.delivery).is_null()` | caught | caught | `&` `|` `~` `.in_()` `.is_null()`; `and`/`or` raise at once | no | low |
| **D. A Python lambda, compiled from its syntax tree** | `s.state == State.CONFIRMED and s.delivery is None` | caught | caught, natively | native — `any(l.qty < 1 for l in s.lines)` is `Through`, `0 <= s.total <= 1000` is a range | yes | medium: a compiler of a subset of Python |

**D** is how C#'s LINQ works — the compiler hands over the lambda's tree — and what Pony ORM and
SQLAlchemy's `lambda_stmt` do in Python. The prototype compiled every case above into the
language's JSON and ran the same lambda in Python. What it has to hold to:

- **One semantics.** The server evaluates the compiled program, never the raw lambda: Python's
  `None > 0` raises where the language answers `false`, as SQL does.
- **A subset, refused loudly.** What the language cannot say — a call, arithmetic until value
  expressions exist — is refused when the model is compiled, naming the construct and its line.
- **Constants are read once.** A name the lambda uses (`State.CONFIRMED`) is read when the model is
  compiled; what changes per call is a dynamic value the compiler recognises (`today()`,
  `context("tenant_id")`).
- **The source has to be there.** A file has it; code loaded at runtime registers its source, as
  `runtime_use_cases` already holds it.

**Built:** `expression_of(Entity, lambda e: …)` (`ddd/criteria/services/predicates.py`) and a
form hint's `When(i.state != …, …)` read from the lambda's tree, held by
`tests/ddd/criteria/test_predicates.py` (golden cases, refusals naming the line, the expression
answering what the lambda would) and `tests/ddd/entity/test_presentation_written.py`. A lambda is
found in its file by its bytecode, or — when it closes over a name — by where its body starts.

**A** and **C** stay as what a program uses to build an expression it does not know in advance —
a filter from a user's input — and the JSON as what clients, agents and stores exchange. A person
writing an entity writes **D**.

## 16. Serving the models

**No new door.** The gateway is already the one place every wire is served from — REST with OpenAPI
3.1, JSON-RPC with OpenRPC, MCP, gRPC — and each wire already publishes a discovery document. The
metamodel feeds those documents; it opens no route of its own.

| A client needs | It gets it from |
|---|---|
| every model of a bounded context | the discovery document its wire already serves: OpenAPI's and OpenRPC's components, MCP's resources — loaded once, revalidated with the metamodel's version as an `ETag` |
| which operations belong to a model — get, search, the select by text, the preview, the edit, the actions | the operations of the same document, tagged by entity (`@entity_tags`), each pointing at the models it reads and answers |
| to read records | the read Commands it already calls (`Get`, `Search`, `LiteralSearch`) with a query as JSON; the query is checked against the model and what was dropped comes back named (`Dropped`) |
| a form's life | `PreviewEntity` and `Edit`, as today; paths by identity (§7) |
| to know its model is stale | every read answers the model's version (`model_version`); a different one means reload the document, which is tagged by its content (`ETag`) and answered `304` while it has not changed |
| an offline bundle, TypeScript types | the export (§10): the same metamodel written to a file |
| models of several contexts on one screen | a BFF merges the documents of the contexts it calls; a model is named `context.Entity`, a reference across contexts too |

Three rules hold it:

1. **What is published is what is exposed.** A model appears in a document only when an operation
   the gateway exposes reads or answers it; `@internal`, `include` and `exclude` decide models as they
   decide operations. Nothing internal leaks by being an entity.
2. **`CMD → bus → Response` stays.** A read answers its records and the model's version — never the
   model again — and nothing about the caller changes what a Command answers.
3. **One description, many renderings.** OpenAPI, OpenRPC, MCP and the export are renderings of the
   same metamodel; none is written by hand and none adds what another lacks.

## 17. Definition of done

Every step closes when:

- its rows in the requirements flip, each held by a named test;
- the parity suites hold: the description of memory and of SQL (from step 0), the expression
  language between Python and TypeScript (extended with `Through` and every new node), the contract
  between Python and TypeScript (from step 4), the lambda compiler against golden cases (from step 2);
- the full suite, pyright and the docs' runnable pages are green, and the consumers' tests run
  against it ([#216](https://github.com/Sincpro-SRL/sincpro_framework/issues/216));
- the pages of the components it touches say only the present, and the persistence skill teaches it;
- what it measured — compile time per model, evaluation per record — is written in §8.
