# Version 4.0.0: exploration

**Production stays on 3.x.** Version 4.0.0 is an exploration, not a planned release. It ships
when the architect is convinced, after reviewing and iterating real projects on it — not on a
date. Until then every service in production keeps running 3.x, and 3.x keeps receiving fixes
and additive features.

## What is being explored

| Area | Question being studied |
|---|---|
| **ORM** | how an aggregate is kept, read and related across stores |
| **Criteria** | the one language a screen, an API, SQL, memory and the client speak |
| **Entrypoints** | how a bus reaches every client: REST, JSON-RPC, gRPC, MCP, CLI, queues |
| **Entity** | what an aggregate declares about itself: structure, how it is read (`DEFAULT_*`), form hints (`presentation`), derivations |
| **Event architecture** | how facts are recorded, stored and delivered |

Each area is studied, analysed and compared against mature frameworks before anything is fixed.
Changes may be disruptive: 4.0.0 is free to break 3.x where the study shows a better shape.

## The spec is the essence; the style may change

What a feature does and why — its spec — is what this phase defines and keeps. How it is
written (a decorator, a class attribute, a function) may change as the study goes on. A PRD in
`docs/prd/` records the spec; the code is its current expression.

## The goals

- **Auto-generative**: the definition of a model and its use cases is enough for a screen, an
  agent or another service to drive it — the ERP built on it generates itself from that.
- **Proven**: every feature is held by tests that simulate real cases, and by projects that use
  it before it is called stable.
- **N architectures**: one database or one per context, a monolith or services, state or events
  — the same vocabulary for each.
- **Clear and elegant**: simple first; one way per idea; the developer reads it once and knows
  it.
- **Open to the bone, with principles**: nothing is a cage — every default can be replaced and
  everything is reachable — but the principles the framework keeps are deliberate, because the
  auto-generative ERP stands on them.

## The shape that stays

- **The backend is the primary product.** Clients — a frontend, MCP agents, a CLI, other
  services — interact with it easily, through what the backend publishes.
- **Non-functional concerns are first-class**: observability, performance, concurrency,
  security hooks, operability.
- **The core is the application layer**: Features and ApplicationServices. Around it sit the
  adapters, the infrastructure and the entrypoints. Everything else serves that layer.
