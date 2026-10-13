# Specifications

A specification says **what the framework covers, why, and how far it has got** — and it is
updated in place, in the same change as the feature. A PRD is a photograph of a proposal; a
specification is the thing a reader trusts today.

| Specification | Covers |
|---|---|
| [Persistence](persistence.md) | The entity model, what a repository reads and writes, analysis from the model, and the status of every capability |
| [Extensibility](extensibility.md) | How a module in another repository adds to an entity it does not own, how releases of the core and of the module are survived, what it costs, and what is missing |

## How a specification is kept

- **One row per capability**, with a status (✅ built · 🟡 partly · ⬜ not built), the page that
  explains it and the test that holds it. A capability with no test is not ✅.
- **A change that adds, moves or removes a capability edits its row** — not a new document. The
  pull request that builds it flips the status.
- **The reason is part of the row's section**: what it is for, what it deliberately does not do,
  and what was rejected. If the reason changes, the section changes.
- **PRDs in [`../prd/`](../prd/) are history**: why something was built the way it was at the time.
  They are not edited to stay true. When one and a specification disagree, the specification is right.
