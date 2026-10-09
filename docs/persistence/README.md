# Persistence in sincpro_framework

How a Sincpro service declares an aggregate, asks for it, brings what is related to it, keeps it,
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
| [Specification](specification.md) | What to bring back of each record, at any depth: the rules, and what the entity declares in `presentation` |
| [Entity reads](entity-reads.md) | `EntityReads`: one Feature for get, get many, the select by text and the list, from the entity's `presentation` |
| [Relations](relations.md) | The paper: the model, the precedence, the resolution algorithm per kind, cost, and how to add a new way to resolve |
| [Transactions](transactions.md) | The unit of work and its options, locks, what the engine refuses and `retrying`, `after_commit`, `upsert` and writes by criteria — runnable |
| [Lifecycle](lifecycle.md) | What runs when something is written, and who each layer reaches |
| [Hooks](hooks.md) | What a project puts around its own aggregates: the moments, dependencies, discovery, what is refused |
| [Events](../events/README.md) | Recorded by the aggregate, published by a Feature, stored by nobody |
| [Persistence reference](reference.md) | The adapter's surface, engines, mapping, observability, what a project owns |
| [Real-world suite](testing.md) | The ledger that proves it under volume, concurrency and a process boundary |
| [Decisions](decisions.md) | Why each piece is the way it is, and what was rejected |

Everything here needs Python 3.12 or later. The vocabulary, `sincpro_framework.ddd`, installs
with the framework. The SQLAlchemy adapter, `sincpro_framework.orm`, is the `[sqlalchemy]` extra.
