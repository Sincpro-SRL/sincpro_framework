# Providers, schemas, requirements, secrets

## `context_provider` — "I want context"

```python
@siat.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
def credentials(context):
    nit = nits.get(context["nit_id"])
    return {"TOKEN": Secret(nit.token), "SIAT_ENV": nit.environment}
```

- Runs when an execution of the bus opens, the context has every key it `needs` and lacks one it
  `gives`. Registration order; registered before the bus is built.
- Its answer lands on **that execution's node**: what it runs finds the keys there, so a nested
  execution does not ask again — once per scope.
- It receives a read-only mapping and returns a mapping (or `None`). Keep I/O out of it when you can;
  when it must read (a NIT record), read through a dependency, not the context.

Replaces the boilerplate of rebuilding credentials at every call site: callers open
`bus.context({"nit_id": nit.id})`.

## `context_schema` — types at the door

```python
class SIATContext(TypedDict, total=False):
    TOKEN: Secret[str]
    SIAT_ENV: SIATEnvironment
    nit_id: str

siat.context_schema(SIATContext)
with siat.context({"TOKEN": "abc", "SIAT_ENV": 2}):   # SIAT_ENV arrives SIATEnvironment.TEST
```

Validates and types the keys the schema names when a scope opens (`bus.context(...)`, including
`global_scope`); any other key passes as given. A `TypedDict` or a DTO. A wrong value raises pydantic's
`ValidationError` at the `with`.

## `requires_context` — declared, then refused

```python
@siat.feature(CommandSendInvoice)
@requires_context("TOKEN", "SIAT_ENV")
class SendInvoice(Feature): ...
```

- Checked after the providers ran, before interceptors and the handler, inside the bus's error
  handling: `ContextRequired(use_case, missing)`.
- A use case that declares nothing is never checked — almost every use case.
- Declare it where running under a default would be dangerous (a call under another tenant's token).

## `Secret`

`pydantic.Secret` (the one settings use). In the context:

- read where it is used: `self.context["TOKEN"].get_secret_value()`;
- masked in `repr`, logs, spans and errors;
- dropped from every header (`inject`), from `to_client()`, and written masked by `TypedCodec`
  unless `TypedCodec(schema, keep_secrets=True)` for a store you trust.
