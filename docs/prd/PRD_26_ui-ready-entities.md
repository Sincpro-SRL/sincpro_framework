# PRD_26: UI-ready entities — what a generic screen or an agent needs, declared once

- **Status**: phase 0 and its criteria follow-ups merged (#153), 2026-10-09. Phase 1 (form
  hints) built on `feat/presentation-field-hints`, not committed. Phases 2-7 are a proposal;
  each is approved on its own before anything is written.
- **Depends on**: the specification and `Meta` (`docs/persistence/specification.md`), Criteria
  and its TypeScript twin `@sincpro/criteria` (one triple, evaluated the same in Python, SQL and
  TypeScript, held by `criteria-parity.json`), hooks, `ordering.py` (Kahn), the auth module
  (`AccessControl.allows/permitted`, hook gates), caching (`KeyValueStore`), the shared context
  (PRD_24).
- **Philosophy**: plug and play, never a cage. The entity says how it is shown (Odoo's
  convenience); what the server enforces, who sees what and what each company needs stay the
  developer's own hooks and client. Typed, not literal: fields are named through the entity
  (`lambda a: a.code`), closed sets are `StrEnum`, no `*,` in signatures.

## 0. Built: presentation and the reads of one aggregate

| Piece | Where |
|---|---|
| `Presentation[T]` on the entity: `display`, `search` (`Match.equal/prefix/contains`), `order`, `detail` (`Expand`, `Reference`) | `ddd/entity/presentation.py`, `docs/persistence/specification.md` |
| `Meta` publishes `display`, `search`, `default_order`, `detail`; `{}` = identity + display | `ddd/entity/model_meta.py` |
| `matching(Model, text)`, `detail_of(Model)`, `get(..., detail=)` | `ddd/entity/query_entity.py`, repositories |
| `Operator.STARTS_WITH`; `%` and `_` are literals in SQL | criteria, `sql_translator.py` |
| `Get` / `GetMany` / `LiteralSearch` / `Search` + `EntityReads[T]` | `ddd/reads.py`, `docs/persistence/entity-reads.md` |
| `ResponseRecord`, `ResponseRecords`, `AggregateNotFound` | `ddd/query.py`, `ddd/exceptions.py` |
| The parity cases run on SQL too; `NOT` is the complement (rows with a NULL included) | `tests/orm/test_criteria_parity_sql.py` |

## 1. Evaluation of what exists

| Piece | Verdict | Why |
|---|---|---|
| `presentation` on the entity | **keep** | It is metadata only and never claims to enforce anything — exactly what Odoo (`_rec_name`, `_order`), Django (`Meta.ordering`) and SAP CDS UI annotations are. The research's main failure (§6.1) is a declaration that *looks* enforced and is not; `presentation` has no such field. |
| Lambdas over the entity | **keep, with one rule** | They run once against a recorder and compile to data (field names, `Criteria`), so what they declare is serializable and publishable. The rule: a declaration lambda only records; it never runs per record. A predicate that can only run in Python is not a declaration. |
| `Meta` cached per class (`describe` is `@cache`) | **keep** | Structure and hints are per class, the same for every caller; nothing about who is asking is written into it. |
| Hooks (`before_save`, dependencies, `self.context`, order, `replaces`, gates) | **the place for server rules** | They already are the per-bounded-context extension point; a rule a project wants kept is a `before_save` hook, which can read a hint's condition so it is written once. |
| Auth (`allows`, `permitted`, `AccessControl.on(hooks)`) | **optional, untouched** | Field access by permission, if ever needed, comes from there, never from the entity. |
| One rule for UI and server | **already the strength** | Every platform studied duplicates conditional rules between a client expression and a server validation, or leaves them UI-only. A Criteria with a parity suite on three evaluators is the shared rule they lack. |

## 2. Scope: what the framework gives, what it leaves to the developer

Simplicity and freedom first: the framework hands tools and defaults, and blocks nothing a
developer may need to do differently. Decided 2026-10-09 after the research in §6.

| Concern | Lives in | What the framework does |
|---|---|---|
| How a record is read and shown — display, search, order, detail, **form hints** | `presentation` on the entity | publishes it on `Meta`; **never enforces it** |
| A rule the server must keep | the project's own hook (`before_save`, `on()` with `after=`/`sequence=`) | runs it; nothing new to learn |
| Who may see or change what (groups, permissions) | the client, or the optional auth module | nothing in the entity |
| What differs per tenant or company | a hook with its dependencies and `self.context` | nothing in the entity |

- **A hint is a default, never a rule.** `readonly`, `required`, `default`, `readonly_when`,
  `required_when`, `visible_when` are what a client starts from. A component that says
  otherwise wins; the API accepts the write either way. This is stated wherever a hint is
  documented, because the failure every platform shares (§6.1) is a declaration that looks
  like a protection.
- **One condition, written once.** A `*_when` condition is a Criteria triple, so the client
  evaluates it with `@sincpro/criteria` and a project hook can read the same condition from
  `presentation_of(Entity)` to enforce it — no second copy to drift.
- **No new hook API.** `on()` stays the one door; a hook that validates is a `before_save`
  hook. A dedicated `constraint()` door or a `validate` moment was weighed and left out.

## 3. Phase 1, built: form hints

```python
@dataclass
class Invoice(Entity):
    partner_id: str
    number: str = "/"
    state: InvoiceState = InvoiceState.DRAFT
    payment: Payment = Payment.CASH
    card_reference: str = ""

    presentation = Presentation["Invoice"](
        readonly=lambda i: (i.number,),
        readonly_when=lambda i: (When(Is(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id),),
        required_when=lambda i: (When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),),
        visible_when=lambda i: (When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),),
    )
```

| Piece | Where |
|---|---|
| `Is`, `When`, `AllHold`, `AnyHolds`; the five hint parts on `Presentation` | `ddd/entity/presentation.py` |
| `FieldMeta.readonly/required/default/readonly_when/required_when/visible_when`; structure-derived defaults (framework fields read-only, no default → required, dataclass default) | `ddd/entity/model_meta.py` (`hinted`, `presentation_of`, `defaults_of`, `framework_fields`) |
| The same hints from a mapped class | `orm/.../model_introspection.py` |
| Spec and recipe | `docs/persistence/specification.md`, skill `references/form-hints.md` |
| Proof: a form simulated from the JSON a client receives, client verdict = server verdict on every state × payment, hints refuse no write, the hook recipe, SQL and bus end to end | `tests/ddd/entity/test_presentation_hints.py`, `tests/orm/test_presentation_hints_sql.py` |

Observed while proving it: a decimal condition value travels as text (`"0"`), so a client reads
each value as its field's type (`FieldMeta.type`, `exact`), as the server does with `Meta.accept`.

## 4. Next phases (proposal)

Each is approved on its own; none enforces a hint by default.

| # | Phase | Delivers |
|---|---|---|
| 2 | **Resolved states on a read** (optional) | `Get` can return, beside the record, each field's hints already evaluated for that record (`readonly: true`), for clients with no Criteria evaluator — a native app, an MCP agent |
| 3 | **What may be chosen** | a Criteria on a relation, added to `matching()` in the select; enforcing it is a project hook |
| 4 | **Actions** | `presentation.actions` with `when`; availability = `matches` ∧, when auth is installed, `allows` |
| 5 | **Preview** | `Preview[T, R]` on `EntityReads`; computed/derived fields with declared dependencies on a transient aggregate, in a context level where writes raise |
| 6 | **Drafts** | stateless by default; `state=DRAFT` records; an optional `KeyValueStore` draft with TTL and version |
| 7 | **Layered presentation** (only if asked) | a tenant or partner overrides presentation hints by layer, SAP-style, hints only |

## 5. Decisions already taken

| Decision | Why |
|---|---|
| Fields named by lambda over the entity, not strings nor class attributes | pyright checks the name and a rename reaches it; a class attribute stops being a token under a default value or a SQLAlchemy mapping |
| One `presentation` class attribute, not several class methods | no collision with fields called `order`, `reading`, `display` |
| Typed DTOs on a Feature base, not `bus.expose(...)` | the project names its DTOs; interceptors, auth and entrypoints treat them as any other |
| `Get` and `GetMany`, not one DTO polymorphic on `id: str \| list[str]` | each contract stays exact; «not found» is 404 for one and `missing` for many |
| Reads generated, writes not | a write is a Command with an intent |
| Form hints in `presentation`, never enforced | simplicity and freedom: a framework that blocks a case the developer needs is a cage; the server rule is the project's hook |
| Conditions as `Is(field, Operator, value)`, not Python operators | explicit, no operator overloading, the same triple a Criteria carries |
| No `constraint()` door, no `validate` moment | `on()` + `before_save` already does it, ordered with `after=`/`sequence=` |
| Groups, permissions and tenants out of the entity | the entity is the same for every caller |

## 6. Research (2026-10)

### 6.1 What looks enforced and is not

- **Odoo 18**: a model field's `readonly=True` is not checked in `write()`; it is a UI default
  and `fields_get` metadata. `required=True` holds through the database's `NOT NULL`. `groups=`
  is enforced (`check_field_access_rights`) and hides the field in `fields_get`. Since 17 the
  view modifiers `invisible`/`readonly`/`required` are Python expressions evaluated by the
  client only. Source: `odoo/models.py` on 18.0, verified in a local checkout.
- **SAP RAP**: "there is no runtime check for mandatory fields"; `IN LOCAL MODE` bypasses the
  static field control.
- **Dataverse**: a column's "business required" holds only in model-driven forms; business
  rules that set requirement or visibility work only at form scope.
- **Payload 3**: `admin.readOnly` "has no effect on the API whatsoever"; 2026 CVEs leaked
  restricted fields through duplicate and join filters.
- **Frappe**: `mandatory_depends_on` / `read_only_depends_on` are client JavaScript; the server
  checks only `reqd`.

### 6.2 What is enforced, and where

- Field access by role is always a separate server layer: Salesforce field-level security
  (Apex in user mode by default since API 67.0), Dataverse field security profiles, Frappe
  `permlevel`, RAP authorization, Hasura column permissions, Directus permission field lists,
  ZenStack `@allow` on a field. Odoo's `groups=` on the field is the exception.
- State-dependent field control computed on the server: only SAP RAP (`features: instance`),
  and its "mandatory" still needs a validation behind it.
- Overrides without code: SAP metadata extensions by layer (CORE < LOCALIZATION < INDUSTRY <
  PARTNER < CUSTOMER, annotations only, owner opt-in), Frappe property setters, Odoo
  `_inherit` and view xpath (fragile across versions).
- Publishing: every platform hands the UI metadata already resolved for the caller —
  `fields_get`, Frappe meta, OData `$metadata`, Salesforce Describe; Hasura even generates a
  schema per role.

### 6.3 What rotted

Rules duplicated between a client expression and a server validation; validation split over
three places (Django model, admin, DRF serializer); silent discards and ambiguous `null`;
imperative `condition` functions nothing can introspect; policy and API availability on two
axes kept in sync by hand (ABP); side paths that skip the rule (Payload's CVEs); the profile ×
record type × layout matrix (Salesforce).

### 6.4 DDD literature

- Invariants on the entity and value objects, checked before the state changes (Khorikov,
  "always-valid"; Microsoft eShop guidance). State-dependent rules as aggregate methods with a
  query twin (`can_confirm()` beside `confirm()`).
- Role and tenant rules outside the aggregate: authorization and configuration (Cosmic Python's
  syntax / semantics / pragmatics split).
- Notification pattern: report every error with the incoming data, not the first (Fowler).
- Specification shared by selection and validation (Evans, Fowler); Khorikov warns against
  generic, unnamed specifications — named rules, not arbitrary filters.

### 6.5 Agents and generic UIs

MCP elicitation takes a flat object of primitives (string, number, boolean, enum); a tool's
JSON Schema carries `readOnly`, `enum`, `required`, `default`. Server-driven UI trends toward
the server sending each field's resolved state. JSON Forms keeps conditions as serializable data
(`HIDE/SHOW/ENABLE/DISABLE` over a schema condition) — the closest published equivalent to a
`*_when` Criteria.

Sources: odoo/odoo 18.0 `models.py`; odoo.com 17.0 view architectures; learning.sap.com RAP
static field control; help.sap.com CDS metadata extensions; learn.microsoft.com Dataverse
business rules and field security; developer.salesforce.com Apex versioned behavior changes;
payloadcms.com fields and access control; zenstack.dev field-level access; hasura.io column
permissions and presets; directus.com access control; abp.io module entity extensions;
docs.frappe.io DocField; enterprisecraftsmanship.com always-valid, validation and DDD,
specification; martinfowler.com Notification; cosmicpython.com appendix E;
modelcontextprotocol.io elicitation; jsonforms.io rules.
