# Persistence in sincpro_framework

How a Sincpro service declares an aggregate, asks for it, brings what is related to it, persists it,
and tells the world what happened, the same way in every service, from one SQLite file to
several databases and several bounded contexts.

New here? [What shape is your system](../shapes.md) picks the wiring before you read the parts.

Read in this order:

| Page | What it answers |
|---|---|
| [Use cases](use-cases.md) | Every database use case, simple to advanced — vocabulary, adapter, SQLAlchemy underneath, and what is still a gap |
| [Landscape](landscape.md) | The state of the art across many stores, events, volume and record APIs — what companies hit migrating, and the roadmap |
| [Manifesto](manifesto.md) | The rules every persistence feature is held to: the port and the type, writing, transactions, money, distributed |
| [**Guide**](guide.md) | **Start here.** Every use case step by step — aggregate, repository, writes, reads, relations, hooks, events, change tracking, event sourcing, outbox, testing — all runnable and tested |
| [Introduction](introduction.md) | What problem this solves, the mental model in one page, a first end-to-end example |
| [Design](design.md) | The two packages, the flow of a read and of a write, the module map, the invariants, where to extend |
| [Criteria](criteria.md) | The boundary language: its grammar, what *reflexive* means, how two criterias merge, what `dropped` is |
| [Extending an entity you do not own](extensions.md) | A module adds fields to a core entity from another repository: each row read as the extension it has, filters by its fields, one flat record of several, the contract it was written against and the kit that proves it |
| [Analytics from the model](analytics.md) | A computed field, the part of a date, groups with no rows and the extra folds: group, filter and order by things that are not columns, answered the same in memory and in SQL |
| [Specification](specification.md) | What to bring back of each record, at any depth: the rules, what the entity answers in its `DEFAULT_*` class methods, and the form hints of `presentation` |
| [Entity reads](entity-reads.md) | `EntityReads`: one Feature for get, get many, the select by text and the list, from the entity's `DEFAULT_*` class methods; what the caller names wins |
| [Preview](preview.md) | What a record becomes before it is saved: derived fields, `assign`, `Preview` on `EntityReads`, and the guard that makes a preview unable to write |
| [Edits](edits.md) | The life of a form in one command: `Edit` saves the caller's draft, saves the record or discards the draft; `Get(draft=True)` / `Search(drafts=True)` find it after the client lost its state; creating and updating, two tabs, someone who saved meanwhile |
| [Drafts](../core/drafts.md) | What a form holds between requests — stored by the context, by user or session, where it stores everything (memory or Redis); optimistic versions, a TTL, `refuse_stale` before activating |
| [Relations](relations.md) | The paper: the model, the precedence, the resolution algorithm per kind, cost, and how to add a new way to resolve |
| [Transactions](transactions.md) | The unit of work and its options, locks, what the engine refuses and `retrying`, `after_commit`, `upsert` and writes by criteria — runnable |
| [Lifecycle](lifecycle.md) | What runs when something is written, and who each layer reaches |
| [Hooks](hooks.md) | What a project puts around its own aggregates: the moments, dependencies, discovery, what is refused |
| [Events](../events/README.md) | Recorded by the aggregate, published by a Feature, stored by nobody |
| [Persistence reference](reference.md) | The adapter's surface, engines, mapping, observability, what a project owns |
| [Real-world suite](testing.md) | The ledger that proves it under volume, concurrency and a process boundary |
| [Decisions](decisions.md) | Why each piece is the way it is, and what was rejected |

Everything here needs Python 3.12 or later. The vocabulary, `sincpro_framework.ddd`, installs
with the framework. The SQLAlchemy adapter, `sincpro_framework.data_layer.orm`, is the `[sqlalchemy]` extra.
