# PRD 30 — A form's whole life, projected onto an ERP

- **Status**: phase 1 built, 2026-10-11 — not committed. Phases 2–4 proposed.
- **Builds on**: PRD_26 (UI-ready entities: preview, derivations, form hints), PRD_27 (default reads),
  PRD_22/24 (the context and its shared store).

## 1. What is built

| Piece | What it does |
|---|---|
| Drafts in `context` | a context kept by whose it is — user (default), session, any keys — in the context's own store (memory or Redis); optimistic versions, a TTL, listable per owner |
| `Edit[T, R]` + `EditAction` | one command for a form's life: `KEEP` the caller's draft (version + preview), `SAVE` it to the store, `DISCARD` it |
| `EntityEdits[T]` | the Feature answering it — an `EntityReads` too; `keep` / `save` / `discard` / `change` / `drafts` to override |
| `Get(draft=True)`, `Search(drafts=True)` | find the draft again after the client lost its state; never implicit |
| `assign(…, adopt=True)`, `form_values` | a line born in a draft keeps its id through every keep and into the database |
| Subjects by bounded context | `billing.Invoice:F-7` — two contexts sharing a store never read each other's drafts |
| Form errors are the caller's | a value `assign` cannot take is a `ClientError` (400), not a crash |

Proved end to end in memory, over a key-value store and on SQLite; a transition (post with a
number) is an `Edit` whose action is `SAVE` — no code of its own (`docs/persistence/edits.md`).

## 2. What the consumers do today

**Synthesis** (pins 3.11–3.12): no built-in read is used; ~15 Features hand-write get / list /
search-events. Editing is per input (`PUT` each one, `PATCH` a status); ids minted by the server;
no autosave; the client keeps a draft in a `Map` that a reload loses (`ui/kit/draft.ts`); the design
system already ships `DirtyState {onSave, onDiscard, saving, blocked}`, unused. Errors are one
sentence (`{error, type}`).

**Forge** (pins 3.16): `EntityReads` everywhere, `presentation` on 9 entities nobody reads yet,
numbering on create (`numbering.take` inside the unit of work). No UI — a CLI that sends the whole
record on update. Every other write is a lifecycle transition with its own event
(`DecideOnIssue`, `CancelIssue`, runs).

**Both lack**: a confirm step that numbers, approvals with an actor and a policy, line operations
without resending the record, attachments in a form, the expected `version` sent by the client,
forms over several aggregates, errors a UI can pin to a field.

## 3. Projected onto an ERP

| ERP need | With what is built |
|---|---|
| A sales invoice edited over days, from two devices | `Edit` KEEP per user; `Get(draft=True)` resumes; `DraftConflict` between tabs |
| A purchase order nobody saved | `Search(drafts=True)`: what each buyer has half done |
| Post / confirm, with its number | a `CommandPostInvoice(Edit)` with `action=SAVE`; the domain's `post` takes the number — never a draft |
| Approve | the same, its access control on the command; the actor is the context's user |
| Multi-company / multi-tenant | the tenant always keys the draft; a company inside a tenant: `drafts(by=["user_id", "company_id"])` |
| An agent over MCP prepares an invoice for a human | the agent keeps it as a draft under the user it acts for; the human finds it in `Search(drafts=True)` and posts it |
| Stock moved by a confirmed order | the order's `post` records an event; the event table and the relay deliver it to inventory (PRD_19) |
| Someone saved the record meanwhile | `stale` on every answer; the save refused with `StaleAggregate` |

## 4. Proposed next

**Phase 2 — errors a form can pin.** A refusal of `assign` names its field in the message only.
`ClientError` gains an optional `field` (`"lines.2.qty"`), carried by every wire (problem details
`errors[]`, JSON-RPC `data`, gRPC `BadRequest`). Synthesis' one-sentence errors become per field.

**Phase 3 — one unit of work for a save.** `EntityEdits.save` reads, checks and writes through
the repository's `context()` when the store transacts, so a number a hook takes and the record it
numbers commit together, and a pessimistic read (`for_update`) is one override away. Today the
optimistic version check already refuses a lost update.

**Phase 4 — adoption.** Synthesis' project screen: `DirtyState` + `Edit` KEEP replaces the volatile
client draft; its hand-written reads become `EntityReads` once it moves to ≥ 3.17. Forge's
`RegisterProject` / `UpdateProject` become one `Edit`; its transitions stay commands.

**Out of scope on purpose.** Forms over several aggregates (a header and its runs) stay a project's
command — `save` is overridable for it. Attachments travel apart and are referenced by id, as both
projects already do. A field-level patch is a project's command (`change`), not a second protocol.

## 5. Decisions open

1. Phase 2 shape: `field` on `ClientError`, or a list of field errors on one refusal (a form sends
   several bad values at once).
2. Phase 3: open the unit of work always, or only when the store transacts and the Feature asks.
3. The draft's TTL per entity (an invoice for days, a cart for hours) — today one per store.
