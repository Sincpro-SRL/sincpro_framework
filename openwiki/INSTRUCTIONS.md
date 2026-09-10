# Documentation instructions — sincpro-framework

Hand-written file. OpenWiki reads it on every run and never rewrites it.

## What this repository is

`sincpro-framework` is a Python library (published to PyPI and Gemfury) for
building applications with DDD, clean and hexagonal architecture. It is not an
application: it has no entrypoint of its own and is never deployed. Other
repositories import it (`sincpro_payments_sdk`, `sincpro_siat_soap`,
`sincpro_mcp_odoo`).

The core is `UseFramework` (`use_bus.py`): a bus where decorated `Feature` and
`ApplicationService` classes are registered, each bound to an input
`DataTransferObject`. The bus is built once (`build_root_bus()`) and then
invoked with a DTO.

## Audience

Programmers who **use** the framework to write an SDK or a service, and
programmers who **maintain** it. Prioritise the first.

## What to document, in this order

1. **How it is used**: registering Features and ApplicationServices, defining
   DTOs, injecting dependencies, building and invoking the bus.
2. **The concepts and how they relate**: Feature vs ApplicationService,
   DataTransferObject, the bus, the dependency container (`ioc.py`, `deps.py`),
   middlewares (`middleware.py`), error handling (`error_handler.py`,
   `exceptions.py`), configuration (`sincpro_conf.py`).
3. **Cross-cutting modules**: `observability/` (tracing and errors), `context/`
   (per-thread context), `introspection/` (reading the registries of an already
   built bus), `entrypoints/` (exposing the bus over MCP and JSON-RPC), `aio/`
   (async bus), `ddd/` (value objects).

## What NOT to do

- **Do not duplicate `docs/architecture/`.** Those documents (`ARCHITECTURE.md`,
  `entrypoint_mcp.md`, `entrypoint_rpc.md`) are hand-written, are the
  authoritative source for design decisions, and are not replaced by this wiki.
  Reference them where relevant; never rewrite or contradict them.
- **Do not document `tests/`** except to illustrate real usage of the public API.
- Do not invent API. If a name, a signature or a behaviour is not in the code,
  do not claim it.

## Content rules

- **Write everything in English**, prose included.
- Every material claim about behaviour must be anchored to the line of code
  that backs it.
- The public API is whatever `sincpro_framework/__init__.py` exports
  (`UseFramework`, `Feature`, `ApplicationService`, `DataTransferObject`,
  `Middleware`, `logger`, `TypeDTO`, `TypeDTOResponse`). Always distinguish that
  surface from internals.
