# PRD_26: UI-ready entities — what a generic screen or an agent needs, declared once

- **Status**: phase 0 built, 2026-10-09 — not committed. Phases 1-5 are a proposal, not approved;
  each phase is approved on its own before anything is written.
- **Depends on**: the specification and `Meta` (`docs/persistence/specification.md`), Criteria
  and its TypeScript twin `@sincpro/criteria` (the same triple evaluated on both sides), hooks,
  `ordering.py` (Kahn), the auth module (`AccessControl.allows/permitted`), caching
  (`KeyValueStore`), the shared context (PRD_24).
- **Philosophy**: plug and play, never a cage. Everything below is declared on the entity or on
  its `presentation`, has a default that works, and stays optional: a Feature written by hand
  keeps working. Typed, not literal: fields are named through the entity (`lambda a: a.code`),
  closed sets are `StrEnum`, no `*,` in signatures.

## 0. Built: presentation and the reads of one aggregate

| Piece | Where |
|---|---|
| `Presentation[T]` on the entity: `display`, `search` (`Match.equal/prefix/contains`), `order`, `detail` (`Expand`, `Reference`) | `ddd/entity/presentation.py`, `docs/persistence/specification.md` |
| `Meta` publishes `display`, `search`, `default_order`, `detail`; `{}` = identity + display | `ddd/entity/model_meta.py` |
| `matching(Model, text)`, `detail_of(Model)`, `get(..., detail=)` | `ddd/entity/query_entity.py`, repositories |
| `Operator.STARTS_WITH`; `%` and `_` are literals in SQL | criteria, `sql_translator.py` |
| `Get` / `GetMany` / `LiteralSearch` / `Search` + `EntityReads[T]` | `ddd/reads.py`, `docs/persistence/entity-reads.md` |
| `ResponseRecord`, `ResponseRecords`, `AggregateNotFound` | `ddd/query.py`, `ddd/exceptions.py` |

Open from phase 0: `@sincpro/criteria` must implement `starts with` and copy
`criteria-parity.json`; `CONTAINS`/`NOT_CONTAINS` in SQL do not escape `%`/`_` yet; the
`make criteria-parity` target points at a path that moved to `tests/ddd/criteria/`.

## 1. The split that decides where each thing goes

| Kind | Example | Declared | Enforced by the server |
|---|---|---|---|
| **Domain** | required, readonly, default, precision, a computed total, a constraint, the states and their transitions | on the field / the entity | yes, on save |
| **Presentation** | how a record is named and found, the list order, the detail, list columns, named filters, the actions shown | `presentation` | no, it is how the record is read |
| **Who is looking** | only accounting edits `account_id` | the auth module (optional) | yes, through `allows` |

A rule that only the UI knows is a lie the API does not keep: every domain attribute that a
screen shows is also checked when the aggregate is saved.

## 2. Phases (proposal)

Ranked by value over effort for an ERP. Research behind each one: §4.

### Phase 1 — static field attributes

`FieldMeta` gains `required`, `readonly`, `default`, and for numbers `precision` and
`currency` (the field holding the currency). Most come from the dataclass itself: no `Optional`
and no default is required; the default is the dataclass default. The rest is a typed object in
`field(metadata=...)`, beside `label` and `help`.

```python
amount: Decimal = field(default=Decimal(0), metadata=Field(precision=2, currency="currency_id"))
```

Open: whether `Field(currency=...)` names the field by lambda too, to keep "typed, not literal".

### Phase 2 — conditions as Criteria, and what may be chosen

- `readonly_when`, `required_when` (domain, checked on save) and `visible_when` (presentation),
  each a `Criteria` over the record. The same triple is evaluated by `matches` on the server and
  by `@sincpro/criteria` on the client, so no JavaScript and no Python is shipped as text.
- Combination: visible = read permission ∧ `visible_when`; readonly = ¬write permission ∨
  `readonly_when` ∨ static readonly; required = (static ∨ `required_when`) only when visible and
  editable.
- **What may be chosen** in a relation (Odoo's many2one `domain`): a `Criteria` declared on the
  relation, added to `matching()` in the select and validated on save. Different from the two
  that exist: `Relation.scope` (what the relation *is*, applied to every read) and a
  specification node's `where` (what one caller asks in one read). A choice that depends on the
  form (the addresses of the customer just picked) needs the form's values: phase 4.

### Phase 3 — actions with `when`, and permissions

`presentation.actions`: a Command tied to the aggregate and to when it applies.

```python
actions=lambda i: (Action(CommandConfirmInvoice, when=Criteria(where=["state", "=", State.DRAFT])),)
```

Availability = `matches(record, when)` ∧ `access.allows(permission of the Command)`; both
pieces exist. A read can say which actions the record has now: what an agent most needs. The
rule itself stays in the entity (confirming a non-draft raises); `when` only announces it.

### Phase 4 — computed fields, preview, and choices that depend on the form

- Computed and derived fields with declared dependencies, by lambda:
  `@computed(depends=lambda i: (i.lines, i.discount))`, and a derived value copied from a
  relation (`derived(lambda i: i.customer.payment_term, mode=Derive.IF_EMPTY)`).
  `Meta` publishes the dependencies, so a screen knows which change asks for a preview.
- `Preview[T, R]`, a fifth DTO answered by `EntityReads`: `values` and `changed` in, the values
  that changed plus warnings and field errors out. With `changed` empty it is the form of a new
  record (Odoo's `default_get`).
- How it runs: the aggregate is built transient from the values; a many2one is read with `get`
  (only read hooks run); computeds are recomputed in dependency order (`ordering.py`); events
  recorded are pulled and discarded; numbering is not consumed.
- **The guarantee**: the preview runs in a marked context level where `save`, `remove` and
  `archive` raise. It cannot write, by construction, rather than by care.
- Rejected: an imperative `@onchange` per field (Odoo itself moves away from it); a write
  inside a transaction that is rolled back (it takes locks, consumes numbering and fires hooks,
  events and mail).

### Phase 5 — drafts across requests, threads and clients

| Model | Who sees it | Use |
|---|---|---|
| A. Stateless: the client holds the form, each preview sends all the values | that client | **the default** — any replica answers, no state on the server |
| B. The draft is a stored record with `state=DRAFT` | everybody | the ERP answer when others must see it before it is official; `version` guards it; nothing new to build |
| C. A draft in a shared store (`KeyValueStore`, TTL, version) | any replica or thread holding its key | multi-step wizards, carts: ephemeral, not worth a table. An addon over the existing stores |
| D. Real-time co-editing | everybody, live | CRDT + websockets — out of the core; at most an event says a draft changed |

Never: a database transaction open across requests, «save and roll back», or a process-local
dict (each replica has its own).

### Later, not phased yet

- `presentation.listing` (list columns) and `presentation.filters` (named filters such as
  «overdue»), published on `Meta`.
- Quick create from a select (Odoo's `name_create`).
- Accent-insensitive search (`unaccent`) for Spanish input.
- Semantic field types (`MONETARY`, `EMAIL`, `PERCENT`) in `FieldType`.
- `Meta` published as JSON Schema 2020-12 with `x-sincpro-*` keywords (depends, `readonly_when`,
  transitions), readable by OpenAPI, MCP elicitation and JSON Forms without an adapter.

## 3. Decisions already taken

| Decision | Why |
|---|---|
| Fields named by lambda over the entity, not strings nor class attributes | pyright checks the name and a rename reaches it; a class attribute stops being a token under a default value or a SQLAlchemy mapping |
| One `presentation` class attribute, not several class methods | no collision with fields called `order`, `reading`, `display` |
| Typed DTOs on a Feature base, not `bus.expose(...)` | the project names its DTOs; interceptors, auth and entrypoints treat them as any other |
| `Get` and `GetMany`, not one DTO polymorphic on `id: str \| list[str]` | each contract stays exact; «not found» is 404 for one and `missing` for many |
| Reads generated, writes not | a write is a Command with an intent |

## 4. How mature systems do it (research, 2026-10)

- **Odoo 18**: `onchange(values, field_names, fields_spec)` runs on `NewId` records in the
  cache, applies `@api.onchange` and `@api.depends` computes, returns `{value, warning}`; with no
  field names it builds a new record. Odoo pushes editable computes (`readonly=False`,
  `precompute=True`) over `@onchange`. `invisible`/`readonly`/`required` are expressions on the
  view since 17. `_rec_name`, `_rec_names_search`, `_order`, `name_create`, many2one `domain`.
  Source: `addons/web/models/models.py`, `odoo/models.py` on the 18.0 branch.
- **Frappe**: `depends_on`, `mandatory_depends_on`, `read_only_depends_on`, `fetch_from`; logic
  in client scripts, which a generated UI or an agent cannot run.
- **Django admin**: `search_fields` with `=`/`^`, `list_display`, `list_filter`,
  `autocomplete_fields`, `limit_choices_to`.
- **JSON Forms**: rules HIDE/SHOW/ENABLE/DISABLE over a condition — the closest published
  equivalent to `*_when` as Criteria.
- **JPA / Prisma**: `@EntityGraph`, `include` — the equivalent of `detail`.
- **MCP elicitation** (spec 2025-11-25): flat primitive schemas, enums as `oneOf` of
  `{const, title}` — what a Command with missing fields could ask for.
