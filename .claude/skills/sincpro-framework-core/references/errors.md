# Error handlers

Source of truth: `sincpro_framework/error_handler.py` and the `add_*_error_handler` docstrings in
`use_bus.py`. The framework repository's README has an "Error Handling" section with more examples.

Three chains, one per scope. Register with the matching method; handlers may be added before or after
the first execution.

```python
framework.add_global_error_handler(global_handler)
framework.add_feature_error_handler(feature_handler)
framework.add_app_service_error_handler(app_service_handler)
```

## How the scopes nest

```text
bus(Command)
└─ global chain            sees whatever leaves the inner scope (and an unknown DTO)
   └─ app service chain    errors raised in an ApplicationService (or that it let through)
      └─ feature chain     errors raised in a Feature or in an interceptor around it
```

A Feature executed by an ApplicationService through `self.feature_bus` fails into the feature chain
first. If that chain re-raises (or there is none), the exception reaches the ApplicationService's
`execute`; uncaught there, the app service chain; re-raised again, the global chain; re-raised from
the last global handler, the caller.

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
one, a handler written only to *watch* returns `None` without meaning to, and the failure is gone.

```python
app.add_global_error_handler(lambda error: log.error(error))
app(SomeCommand())          # returns None. The failure is gone.
```

A handler that watches must re-raise (`raise error`) so the next handler runs.

Two consequences that give no error signal:

- **`return_type` is not checked.** `bus(dto, ResponseX)` returns whatever a handler answered (a
  dict, `None`) even though the caller's type says `ResponseX`.
- **A feature handler answers inside orchestrations too.** In an ApplicationService,
  `self.feature_bus(CommandStep(...), ResponseStep)` returns the feature handler's answer, and the
  ApplicationService continues with it. Prefer answering in the global chain, where the answer goes
  straight to the caller.

## Where handlers do and do not apply

- Handlers wrap Features/ApplicationServices and the interceptors around them, **not** the code that
  calls the bus. An exception in a wrapper or a transport around the bus is not seen by them.
  Unexpected errors must be raised from a Feature.
- A DTO no handler answers raises `UnknownDTOToExecute`, which the global chain also receives: a
  catch-all global handler turns a missing registration into an ordinary answer.
- Expected traffic (validation, "already exists", auth, idempotency refusals) goes in
  `bus.ignore_sentry_exceptions(...)` (an `isinstance` match, so subclasses count): not sent to
  Sentry, logged at info, metric outcome `expected`. Handlers still run. Anything not in the list is
  reported even when a handler answers it. An error-rate alert reads `sincpro_outcome!~"ok|expected"`.
- A failure answered by a handler is still recorded on `sincpro.use_case.duration` by its kind.

## Ordering and switching off

Like interceptors, handlers take `replaces=`, `before=`, `after=` and `sequence=` (lower first, 10
default), then registration order. `framework.without_error_handler(fn)` switches one off, in any
of the three chains. What cannot be done as asked (replacing a handler not registered) is a warning
logged when the bus builds, never an exception.
