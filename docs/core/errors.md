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

**What the caller reads.** The message of a `ClientError` or a `DomainError` is written for the
caller and told to it; anything else is answered by its kind alone.

## A project's own errors

Subclass the base that says who caused it; declare `failure_kind` when it means another thing than
the base:

```python
from sincpro_framework import ClientError, FailureKind, ServiceUnavailableError
from sincpro_framework.ddd import DomainError


class InvoiceNotFound(ClientError):
    failure_kind = FailureKind.NOT_FOUND


class InvoiceDoesNotBalance(DomainError):
    pass


class TaxAuthorityDown(ServiceUnavailableError):
    pass
```

## What the framework raises

A subclass exists only when someone catches it by name, when it carries data, or when it changes
what to do; anything else is its base with a clear message.

| Base | Subclasses | Kind |
|---|---|---|
| `ProgrammingError` | `DTOAlreadyRegistered`, `DependencyAlreadyRegistered`, `DependencyNotRegistered`, `UnknownDTOToExecute`, `BusAlreadyBuilt`, `SincproFrameworkNotBuilt`, `ContextRequired` (`missing`), `BusAlreadyRegistered`, `BusNotRegistered`, `RelationNotResolved`, `MigrationRefused`, `WorkflowsInvalid` (`issues`), `InvalidAddress` | internal |
| `ClientError` | `InvalidCriteria`, `DTODoesNotFit` (`fields`), `UseCaseRefused` | invalid |
| | `AggregateNotFound` | not_found |
| | `AuthError` → `Unauthenticated`, `PermissionDenied` | unauthenticated, permission_denied |
| | `AlreadyInProgress`, `KeyReused` | in_progress, key_reused |
| `DomainError` | `ConstraintViolation`, `WorkflowFailed` (`run`) | domain |
| | `StaleAggregate`, `DuplicateAggregate`, `TransactionConflict`, `DraftConflict` | conflict |
| `ExternalServiceError` | `ContextFailed` (the remote class), `MigrationFailed` | internal |
| `ServiceUnavailableError` | `ContextUnavailable`, `TimedOut`, `StoreUnavailable` (a Redis or Memcached store), a broker that does not take an event | unavailable |
| `OutcomeUnknownError` | `ContextOutcomeUnknown` → `ContextTimeout` | unknown_outcome |

Where Python already has the word, the framework mixes it in so an existing `except` keeps working:
`DependencyNotRegistered` is an `AttributeError`, `BusNotRegistered` a `LookupError`,
`InvalidAddress` a `ValueError`. A plain `TypeError` or `ValueError` for an argument of the wrong
type or value stays Python's own — it is what every library raises for it.
