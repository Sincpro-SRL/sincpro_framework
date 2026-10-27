# Errors

Every error the framework raises on purpose says **who caused it** by its base, and **what to do
about it** by its `failure_kind`. Every wire — REST, JSON-RPC, gRPC, MCP, a queue — and the
`ExecutionFailed` event answer with that kind, each in its own code.

```
FrameworkError
├── ProgrammingError          the developer: the code or its configuration   internal          → fix the code
├── ClientError               the caller: what it sent is wrong              invalid           → fix the request
├── DomainError (ddd)         a rule of the business says no                 domain            → show the answer
└── ExternalServiceError      something this call depends on failed          internal
    ├── ServiceUnavailableError   it did not answer                          unavailable       → retry later
    └── OutcomeUnknownError       it may have run                            unknown_outcome   → verify, then retry
```

**A crash is everything else.** An exception that is not a `FrameworkError` — a `KeyError`, a
`ZeroDivisionError`, a driver's error nobody translated — is a bug nobody foresaw: `internal`,
logged with its traceback and reported to GlitchTip, and nothing of it is told to the caller. There
is no class for it; not being one of these is what makes it one.

**A fault or an answer — decided once, read by every signal** (`common.failures.is_fault`). A
crash, a `ProgrammingError`, a service that did not answer are faults: GlitchTip, an `error` log
line with its traceback, a span in error, `sincpro.fault="true"`, `ExecutionFailed.fault`. A
`ClientError` or a `DomainError` is written for the caller — an answer, not a bug: an `info` line,
nothing more, its kind still on every signal (`sincpro.outcome`). A bus turns the rule for its own
errors with `ignore_sentry_exceptions(...)` and `report_sentry_exceptions(...)`.

**What the caller reads.** The message of a `ClientError` or a `DomainError` is written for the
caller and told to it; anything else is answered by its kind alone.

## One problem, on every wire

Every failure is told as one `Problem` (`problem_of(error)`), and every wire draws that same
problem in its own encoding — so a client, a person or an agent reads the same kind, the same next
step and the same wait whichever way it called:

```python
from sincpro_framework import NextStep, problem_of

problem = problem_of(InvoiceNotFound("invoice F-9 does not exist"))
problem.kind          # FailureKind.NOT_FOUND
problem.reason        # "INVOICE_NOT_FOUND" — stable, what a client switches on
problem.status        # 404
problem.detail        # "invoice F-9 does not exist" — only what was written for the caller
problem.next_step     # NextStep.DO_NOT_RETRY
problem.guidance      # "What the request names does not exist: check the identifier; …"
problem.retryable     # False
problem.retry_after   # None — a timedelta when waiting helps; seconds as JSON
problem.errors        # the fields that did not validate, each a JSON pointer
problem.extensions    # what the error disclosed besides: a missing permission, a step-up
```

| Kind | `next_step` | Status |
|---|---|---|
| `invalid` | `fix_request` | 422 |
| `unauthenticated` | `authenticate` | 401 |
| `permission_denied` | `ask_for_access` | 403 |
| `not_found`, `domain` | `do_not_retry` | 404, 422 |
| `conflict` | `refresh_and_retry` (a `DuplicateAggregate`: `do_not_retry`) | 409 |
| `in_progress`, `exhausted`, `unavailable` | `retry_later`, with `retry_after` | 409, 429, 503 |
| `key_reused` | `fix_request` — a new request needs a new key | 422 |
| `unknown_outcome` | `verify_then_retry` | 504 |
| `internal` | `retry_later` — nothing of the inside is told | 500 |

| Wire | Where the problem goes |
|---|---|
| REST on FastAPI | the RFC 9457 body (`application/problem+json`), `Retry-After`, `WWW-Authenticate` on a 401 |
| `RestGateway` | the same members under `error`, the same headers |
| JSON-RPC | the error's `data`: `kind`, `reason`, `retryable`, `next_step`, `guidance`, `retry_after`, `message` |
| gRPC | `google.rpc.Status`: `ErrorInfo` (`reason`, `metadata.kind`, `next_step`, `guidance`), `RetryInfo`, `BadRequest` |
| MCP | a tool error (`isError`): the problem as structured content, and a text a model reads — what happened and what to do next |

**Changing what an error means — declared on the error, never in a table.** The kind gives the
defaults; an error says more when it knows more:

```python
from sincpro_framework import exceptions, NextStep, Problem


class InvoiceNumberMistyped(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.NOT_FOUND
    next_step = NextStep.FIX_REQUEST                      # what the caller does
    guidance = "Check the invoice number on the printed copy."


class OverCreditLimit(ddd_exceptions.DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"the customer's credit limit is {limit}")
        self.limit = limit

    def refine_problem(self, problem: Problem) -> Problem:   # anything else
        return problem.model_copy(update={"extensions": {"limit": self.limit}})
```

An error a project does not own — a library's — is translated into one of its own by an error
handler of the bus; from there it is any other error. A wire of the project's own draws the same
problem without a table of its own:

```python
problem = problem_of(error)
return JSONResponse(problem.model_dump(mode="json", exclude_none=True), status_code=problem.status)
```

An MCP server built with `tool_function` answers a failure with `tool_error` by default; another
rendering is its `on_failure`.

## A project's own errors

Subclass the base that says who caused it; declare `failure_kind` when it means another thing than
the base:

```python
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework import exceptions


class InvoiceNotFound(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.NOT_FOUND


class InvoiceDoesNotBalance(ddd_exceptions.DomainError):
    pass


class TaxAuthorityDown(exceptions.ServiceUnavailableError):
    pass
```

## What the framework raises

A subclass exists only when someone catches it by name, when it carries data, or when it changes
what to do; anything else is its base with a clear message.

| Base | Subclasses | Kind |
|---|---|---|
| `ProgrammingError` | `DTOAlreadyRegistered`, `DependencyAlreadyRegistered`, `DependencyNotRegistered`, `UnknownDTOToExecute`, `BusAlreadyBuilt`, `SincproFrameworkNotBuilt`, `ContextRequired` (`missing`), `BusAlreadyRegistered`, `BusNotRegistered`, `RelationNotResolved`, `MigrationRefused`, `InvalidAddress` | internal |
| `ClientError` | `InvalidCriteria`, `DTODoesNotFit` (`fields`), `UseCaseRefused`, a form's value `assign` cannot take | invalid |
| | `DraftConflict` (`current`) | conflict |
| | `AggregateNotFound` | not_found |
| | `AuthError` → `Unauthenticated`, `PermissionDenied` | unauthenticated, permission_denied |
| | `AlreadyInProgress`, `KeyReused` | in_progress, key_reused |
| `DomainError` | `ConstraintViolation` | domain |
| | `StaleAggregate`, `DuplicateAggregate`, `TransactionConflict` | conflict |
| `ExternalServiceError` | `ContextFailed` (the remote class), `MigrationFailed` | internal |
| `ServiceUnavailableError` | `ContextUnavailable`, `TimedOut`, `StoreUnavailable` (a Redis or Memcached store), a broker that does not take an event | unavailable |
| `OutcomeUnknownError` | `ContextOutcomeUnknown` → `ContextTimeout` | unknown_outcome |

Where Python already has the word, the framework mixes it in so an existing `except` still works:
`DependencyNotRegistered` is an `AttributeError`, `BusNotRegistered` a `LookupError`,
`InvalidAddress` a `ValueError`. A plain `TypeError` or `ValueError` for an argument of the wrong
type or value stays Python's own — it is what every library raises for it.
