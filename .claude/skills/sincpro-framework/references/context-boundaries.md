# Context boundaries — what may import what, and why

Where a module goes and which imports are allowed. Folder names and bootstrap are in
[module-structure.md](module-structure.md); this is the reasoning that decides their contents.

## The rules the framework checks

`sincpro_framework.runtime.testing.layer_violations("my_service")` reads the source (without importing it)
and reports every import that breaks one of these rules. Keep it at `[]` in the suite; a project
that does not follow a rule passes its name in `ignore=`.

| Rule | What it says |
|---|---|
| `domain-is-vocabulary` | within a context, `domain/` imports only `domain/` |
| `adapters-are-independent` | files inside one folder under `adapters/` may import each other; that folder exposes one facade. A different adapter module is not imported |
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
| Pure invariants and rules on a model | `domain/`, as a method of that model | Belongs with the thing it constrains — never a loose function |
| A port (`typing.Protocol`) the use cases call: typed in `dependencies.py`, called from `services/` | `domain/` | The abstraction, not the mechanism |
| A contract only an adapter calls: what a facade holds its implementations by | `adapters/<module>/`, beside the facade | Only the mechanism uses it; it is not the domain's vocabulary |
| A detector, classifier or heuristic | `adapters/` | Swap it and the results change |
| An encoder, a serialisation strategy | `adapters/` | Swap it and what downstream learns changes |
| A statistical or numerical kernel | `adapters/` | Swap it and the output distribution changes |
| A repository implementation, a client, a store | `adapters/` | Always |
| Bus wiring, dependencies, tables, observability | `infrastructure/` | Swap it and nothing about the business changes |
| The context's settings | `<ctx>/settings.py` (a shape shared by several contexts: `common/settings.py`) | Read once, typed; `sincpro-framework-settings` |

A thousand lines of computation with no I/O is still an adapter. "Pure" is not the test —
*replaceability with behavioural consequence* is. A numerical kernel in `domain/` is the common
mistake: it looks pure, and it strains "vocabulary" past breaking.

With the mechanism behind a port, `dependencies.py` is the only file that chooses: a vendor
library, a legacy implementation or a faster rewrite arrives as a second adapter and no Feature
changes.

## One adapter module, one facade

An adapter module is the first folder under `adapters/` (`adapters/hosts/`, `adapters/odoo/`).
Files inside that folder may import each other: they are one mechanism, split so each file stays
readable. The folder exposes one facade — or one proxy — and that class is the API the rest of
the context calls. `dependencies.py` registers the facade. A Feature calls the facade. Nothing
outside the folder imports the pieces behind it.

```
adapters/hosts/
  __init__.py          # exports HostDirectory, the facade
  directory.py         # may import local, ssh, kubernetes_pod
  local.py
  ssh.py
  kubernetes_pod.py
```

`layer_violations` already treats that folder as one unit. The rule `adapters-are-independent`
compares the first name under `adapters/`. Imports inside `hosts/` are clean.
`adapters/host_transport.py` importing `adapters/hosts/` is two adapter modules, and the check
reports it. A file sitting directly in `adapters/` (`adapters/mail.py`) is its own unit; to share
imports with siblings, put them in a folder and export the facade from it.

Two adapter modules that need each other are composed by a Feature, not by one module reaching
into the other. The record they exchange lives in `domain/`. An adapter may import a port its
context's `domain/` declares: that is the abstraction it implements, not a peer it calls.

**A `Protocol` lives with its consumer.** Ask who calls its methods. A Feature, an
ApplicationService or the domain → it is a port, in `domain/`. Only the adapter → it is part of
the mechanism, in the adapter's folder. A facade that picks one implementation by name
(`ChannelDirectory.of("github")`, `SandboxProviders.get("odoo")`) holds them by a `Protocol`
that no Feature names: the Feature calls the facade. That `Protocol` goes beside the facade:

```
adapters/channels/
  __init__.py          # exports ChannelDirectory, the facade
  channel.py           # class Channel(Protocol): the contract of its implementations
  directory.py         # ChannelDirectory: dict[str, Channel]
  github.py
  webhook.py
```

A `Protocol` in `domain/` that only files under `adapters/` import is a misplaced one.

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
- Does an adapter module import a different adapter module? Inside one folder, do outsiders call the facade?
- Is there a mechanism sitting in `domain/` because it happens to be pure?
- Does any adapter loop over a collection that a Feature should be walking?
