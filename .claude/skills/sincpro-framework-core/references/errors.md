# Error handlers

Depth: the README "Error Handling" section and `error_handler.py`.

Three independent scopes: **global** (the root bus), **feature**, **app service**. Register with the
matching method; handlers may be added before or after the first execution.

```python
framework.add_global_error_handler(global_handler)
framework.add_feature_error_handler(feature_handler)
framework.add_app_service_error_handler(app_service_handler)
```

## Chaining

Every call adds to a chain. The **first registered runs first**; if it re-raises, the framework
delegates to the next.

```python
def auth_handler(error):
    if isinstance(error, AuthenticationError):
        return {"ok": False, "detail": "unauthenticated", "code": 401}
    raise error                              # delegate to the next handler

def observability_handler(error):
    log.error("unhandled error", exc_info=error)
    raise error                              # delegate

def base_handler(error):
    return {"ok": False, "detail": str(error)}   # last: always answers

framework.add_global_error_handler(auth_handler)          # 1st = runs first
framework.add_global_error_handler(observability_handler) # 2nd
framework.add_global_error_handler(base_handler)          # 3rd = final fallback
```

## The swallow trap

**What a handler returns becomes the bus's answer.** With no handler the exception propagates. With
one, a handler written only to *watch* returns `None` without meaning to — and the failure is gone
and nobody knows.

```python
app.add_global_error_handler(lambda error: log.error(error))
app(SomeCommand())          # returns None. The failure is gone.
```

A handler that watches must re-raise (`raise error`) so the next handler runs. Documented on the
three `add_*_error_handler` methods and pinned by tests.

## Where handlers do and do not apply

- Handlers wrap Features/ApplicationServices, **not** the code that calls the bus. An exception in a
  wrapper/middleware around the bus — or in a transport — is not seen by them. Unexpected errors must
  be raised from a Feature.
- Expected traffic (validation, "already exists", auth, idempotency refusals) goes in
  `bus.ignore_sentry_exceptions(...)`: not reported, and its metric outcome is `expected`. An
  error-rate alert then reads `sincpro_outcome!~"ok|expected"`.
- A failure answered by a handler is still recorded on `sincpro.use_case.duration` by its kind.

## Ordering

Like interceptors, handlers take `replaces=`, `before=`, `after=` and `sequence=`.
