# PRD_27: the entity answers how it is read — `DEFAULT_*` class methods

- **Status**: built on `feat/entity-default-hooks`, 2026-10-09. Replaces the read half of
  PRD_26 §0 (`presentation`'s `display`, `search`, `order`, `detail`); form hints stay in
  `presentation` as PRD_26 §3 built them.
- **Found by**: Forge, the first project on `EntityReads`. It ended up with four places that
  decided what a `Get` brings — `presentation.detail`, a `default_factory` on each DTO's
  `criteria`, the caller's criteria, an override of `get()` — and a hidden precedence among
  them (a declared detail was a ceiling the caller could only cut).
- **Depends on**: the specification and `Meta` (`docs/persistence/specification.md`),
  `EntityReads` (`docs/persistence/entity-reads.md`), Criteria.

## 1. The problem

| Observed | Why it was wrong |
|---|---|
| `presentation` held form hints **and** query behavior (`order` changed every read, nested ones included; `detail` decided what `Get` returned; `search` built a filter) | A part named "presentation" changed what the store answers. Nobody reading it expects a FIFO queue to depend on it. |
| `detail` was a small language (`Expand` one level deep, `Reference`, bare fields) beside `Specification` | Less than what it compiled to: no depth, no "every scalar plus this relation". Projects fell back to a `reading()` staticmethod and a `default_factory` on every DTO. |
| `Get` combined detail and caller with `merged_with`, which intersects the specification | A caller could never ask for more than the default. Right for a scope (a mask only takes away), wrong for a default. |
| `Get` always found a record by `id` | A project with a natural key had to override `get()` or make the key its `id`. |
| The PRD_26 reason for one attribute — "no collision with fields called `order`, `reading`, `display`" | It solved a naming collision, not where each responsibility lives. |

## 2. The rules

1. **The entity is the source of truth for how it is read.** Five class methods on `Entity`,
   each with a default; a subclass overrides what it needs.
2. **What a caller's criteria names wins, part by part.** Its `specification`, `order`,
   `pagination`… replace the entity's; its `where` adds to the read's own filter.
   "Names" is `model_fields_set`, so `Criteria()` keeps every default.
3. **`Get` and `GetMany` find a record by `DEFAULT_GET_ID`**, `"id"` unless overridden.
4. **`presentation` is for clients that build forms**: `readonly`, `required`, `*_when`.

## 3. The class methods

| Class method | Answers | `Entity`'s default | Read by |
|---|---|---|---|
| `DEFAULT_GET_ID()` | the field `Get`/`GetMany`/`Preview` find a record by | `"id"` | `EntityReads`, `Meta.get_id` |
| `DEFAULT_READING()` | what one record brings, at any depth: a `Specification` | `None` (every scalar, no relation) | `Get`, `GetMany`, `Search`, preview, `detail_of`, `Meta.detail` |
| `DEFAULT_ORDER()` | the order of a list and of this entity's records held by another | newest `id` first | the stores (`Meta.default_order`), nested relations included |
| `DEFAULT_DISPLAY()` | the field shown beside the identity (specification rule 8) | `name` when there is one | references, selects, `Meta.display` |
| `DEFAULT_LITERAL_SEARCH()` | a template: a `Criteria` whose `where` holds `TEXT` | containment on a text display, else `None` | `matching`, `LiteralSearch`, `Meta.search` |

- **Upper case**, so it never meets a field and a reader knows the framework calls it.
- **`@classmethod` only.** A value, a `@staticmethod` or a field with one of these names is
  refused when the class is declared; a class method is what a type checker accepts as an
  override of `Entity`'s.
- **`DEFAULT_READING` is a `Specification`, not a `Criteria`.** It says what of each record,
  never which records: a `where` there would hide records from every read. A node of the
  specification is a criteria, so order, page and depth of a relation live in it.
- **`DEFAULT_LITERAL_SEARCH` is a template, not a function of the text.** `Meta` publishes it
  and a client fills `TEXT` the way `matching` does. `TEXT` may stand inside a list
  (`code in [TEXT, "0"]`). A blank literal drops the conditions holding `TEXT` and keeps the
  rest.
- **The key is how a record is asked for, not its identity.** Storage, relations, ordering
  tiebreakers and cursors still use `id`.

## 4. What is checked, and when

When the class is described (import, first `describe`):

| Refused | Message names |
|---|---|
| a key, display or order on a field that does not exist | the class method and the field |
| a key that is not a text, integer or uuid field | its type |
| a display that is a relation or embedded | its type |
| a reading that is not a `Specification`, or names an unknown first-level field | the field (deeper names are dropped and reported by the store, rule 7) |
| a template that never uses `TEXT`, sets anything besides `where`, or asks a prefix/containment a field cannot answer | the field and operator |
| a mapped class whose `DEFAULT_GET_ID` (other than the identity) is not unique on disk | the table and column |

When the class is declared: a `DEFAULT_*` that is not a `@classmethod`, or a field named like one.

## 5. Combining a default with a caller

`Criteria.replaced_by(other)` — the default with every part `other` names in its place, filters
accumulated. `merged_with` keeps its intersection (scopes, units of work, relation scopes) and
now carries only what either side named, so a scope merged into a caller's criteria never
hides the entity's defaults.

| Read | Criteria |
|---|---|
| `Get(id)` | `READING.replaced_by(asked)` + `where key = id`, limit 1, no count |
| `GetMany(ids)` | the same with `key in ids`; `missing` by key |
| `Search` | `READING.replaced_by(asked)`; the store applies `DEFAULT_ORDER` when no order is named |
| `LiteralSearch(text)` | the template filled, `{}` (identity + display), limit 8, `.replaced_by(asked)` |

## 6. Removed

`Presentation(display=, search=, order=, detail=)`, `Match`, `MatchMode`, `Expand`,
`Reference`, `Descending`. `Meta.search` is now the template (`Criteria | None`), not a tuple of
`Match`; `Meta.get_id` is new.

## 7. Open, for the architect

| # | Question | Today |
|---|---|---|
| 1 | Does `@sincpro/criteria` (TypeScript) read `Meta.search`? It changed shape. | Not verified from this repo. |
| 2 | Should a list read lighter than a detail (`DEFAULT_LISTING`)? | `Search` uses `DEFAULT_READING`; a caller wanting less names a specification. |
| 3 | Version: this breaks `presentation`'s read parts from 3.15.0. | Only Forge is known to use them. |

## 8. Proof

| Case | Test |
|---|---|
| defaults with nothing declared; overrides; inheritance of the ones not overridden | `tests/ddd/entity/test_query_entity.py`, `tests/ddd/test_reads.py` |
| key other than `id` for `Get`/`GetMany`, `missing` by key | `tests/ddd/test_reads.py` |
| caller's specification replaces the reading (even asking for more), `Criteria()` keeps it, a caller filter adds to the key | `tests/ddd/test_reads.py` |
| a scope merged with `merged_with` keeps the defaults | `tests/ddd/test_reads.py` |
| template filled, blank literal keeps the rest of the template, `TEXT` inside a list | `tests/ddd/test_reads.py` |
| every refusal of §4 | `tests/ddd/test_reads.py`, `tests/ddd/entity/test_query_entity.py`, `tests/orm/test_presentation_sql.py` |
| a relation comes in its own entity's `DEFAULT_ORDER` on SQL | `tests/orm/test_presentation_sql.py` |
| Forge (213 tests) on this branch | adopted in Forge's working tree |
