# Context boundaries — what may import what, and why

Where a module goes and which imports are allowed. Folder names and bootstrap are in
[module-structure.md](module-structure.md); this is the reasoning that decides their contents.

## The rules the framework checks

`sincpro_framework.testing.layer_violations("my_service")` reads the source (without importing it)
and reports every import that breaks one of these rules. Keep it at `[]` in the suite; a project
that does not follow a rule passes its name in `ignore=`.

| Rule | What it says |
|---|---|
| `domain-is-vocabulary` | within a context, `domain/` imports only `domain/` |
| `adapters-are-independent` | within a context, an adapter never imports a different adapter |
| `services-reused-through-bus` | no handler class and no function is imported from a service module; its DTO goes on the bus |
| `entrypoints-are-outermost` | only `entrypoints/` imports `entrypoints/` |
| `contexts-are-acyclic` | two contexts never depend on each other |
| `common-imports-no-context` | a context named `common` imports no sibling context |

Everything below is the judgement those checks cannot make.

## The dependency graph is layered, not flat

Contexts form a chain, each depending only on more foundational ones:

```
execution → planning → catalog → common
```

A context **may** import a lower context's `domain/` — that is what the edge is for. It never
depends upward or sideways, and `common/` depends on no other context. The rule is direction, not
abstinence: forbidding every cross-context import pushes shared-looking types into `common/` where
they do not belong.

A context reaches a lower context's **use cases** through that context's bus, injected as a
dependency — never by importing its `services/` or `adapters/`:

```python
# infrastructure/dependencies.py of `sales`
from my_service.domains.common import common

framework.add_dependency("common", common)

# a Feature of `sales`
connection = self.common(CommandResolveTenant(access_token=dto.access_token), ResponseResolveTenant)
```

### The test that settles it: extraction

**Could this context be lifted into its own service by taking it plus the layers below it?**

If yes, the boundary is real. If extracting it would drag a sibling along, or half of another
context's internals, the boundary is decoration.

A DTO that crosses an edge becomes a **wire contract** on the day of the split. That is a reason to
leave it in the **producing** context — versioned with what produces it — rather than in `common/`,
where every context would share one version and be coupled through it.

The further up the chain a context sits, the more its dependency can become a *payload* instead of
a *call*: a worker that receives the plan in its Command needs no other service running at all,
which is the most extractable shape for the expensive component.

### Too many imports is a modelling smell

Count the symbols crossing each edge. Two or three DTOs flowing producer → consumer is a contract.
A dozen, or imports flowing both ways, means the split is in the wrong place — fix the model, not
the imports.

## `common/` is for the genuinely universal

Three questions, in order:

1. **Do two or more contexts use it?** One consumer is not two. A module with a single consumer
   belongs to that consumer, however general its name sounds.
2. **Do they mean the same thing by it?** Not "does it have the same shape" — does it carry the
   same meaning, and would divergence be a bug rather than a feature?
3. **Would a change to it be a change for everyone?** If one context would want it to evolve
   differently, it is not shared.

### Repetition across contexts is correct, not debt

`SaleCustomer` and `B2BCustomer` are both customers. They are **not** one `Customer`: the fields
overlap, the meanings do not — different invariants, lifecycles and reasons to change. Collapsing
them couples two contexts through a model neither owns.

Duplicate the entity. Go to `common/` only when the thing is genuinely identical, never to avoid
typing it twice. The pull toward a shared model is strongest exactly when it is most wrong: two
young contexts look similar. Wait for them to diverge; that is information.

## `domain/` holds what things are; `adapters/` holds how things get done

The discriminator: **would replacing this with something equivalent change business behaviour?**
Yes → `adapters/`. No, and it is wiring → `infrastructure/`.

| Module | Layer | Why |
|---|---|---|
| DTOs shared by use cases, aggregates, value objects | `domain/` | Vocabulary |
| Policy constants and thresholds | `domain/` | The business decides these; a mechanism applies them |
| Pure invariants and rules on a model | `domain/` | Belongs with the thing it constrains |
| A port (`typing.Protocol`) an adapter implements | `domain/` | The abstraction, not the mechanism |
| A detector, classifier or heuristic | `adapters/` | Swap it and the results change |
| An encoder, a serialisation strategy | `adapters/` | Swap it and what downstream learns changes |
| A statistical or numerical kernel | `adapters/` | Swap it and the output distribution changes |
| A repository implementation, a client, a store | `adapters/` | Always |
| Bus wiring, dependencies, tables, settings, observability | `infrastructure/` | Swap it and nothing about the business changes |

A thousand lines of computation with no I/O is still an adapter. "Pure" is not the test —
*replaceability with behavioural consequence* is. A numerical kernel in `domain/` is the common
mistake: it looks pure, and it strains "vocabulary" past breaking.

With the mechanism behind a port, `dependencies.py` is the only file that chooses: a vendor
library, a legacy implementation or a faster rewrite arrives as a second adapter and no Feature
changes.

## An adapter never imports another adapter

Two adapters that need each other are two adapters being **composed**, and composition is a
Feature's job. An adapter reaching for a peer puts orchestration where nothing traces it and makes
the pair un-swappable.

The fix is almost always the same:

- the **protocol or record** moves into `domain/` (the rules, the invariants, the key layout);
- the **mechanism** stays in its own adapter;
- the **Feature** calls both, in order, and owns the sequence.

An adapter may import a port its context's `domain/` declares: that is the abstraction it
implements, not a peer it calls.

## Adapters expose a rich API; Features orchestrate

An adapter answers **one thing well** — classify *a* column, encode *a* value, fetch *a* record. A
Feature walks the collection, sequences the calls, and attaches identity and provenance.
`classifier.classify(profile) -> Judgment` is the adapter; assembling every column's judgment and
stamping it with what was classified is the Feature. A loop inside the adapter buries orchestration
where it is neither traced nor reusable.

A `Feature` composes **adapters**; an `ApplicationService` composes two or more **use cases**
through `self.feature_bus`. Both keep the logic in `services/`, where it is observable.

## Review checklist

- `layer_violations("<package>") == []`.
- Does every cross-context import point **downward** in the chain?
- Would each context still extract cleanly as a service?
- Does anything in `common/` have exactly one consumer? Move it to that consumer.
- Is anything in `common/` shared only because the shapes matched? Duplicate it instead.
- Does any adapter import another adapter?
- Is there a mechanism sitting in `domain/` because it happens to be pure?
- Does any adapter loop over a collection that a Feature should be walking?
