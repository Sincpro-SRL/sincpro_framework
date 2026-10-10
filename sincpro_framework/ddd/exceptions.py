"""What the vocabulary refuses, in its own words.

A caller only ever needs to tell these apart: what it asked for cannot be answered, the contract
is being used wrong — by the code, or by data the database refuses — or a write lost a race:
against a newer version of the same aggregate, against another aggregate already holding a
unique value, or against another transaction — or the engine stopped at a bound it was given —
or the one record a read named is not there.
A stale version and a lost transaction are the ones worth running again.

They inherit from `Exception` and nothing else. Every wire answers them through one
classification (`common.failures.refined_failure_kind`): `StaleAggregate` and
`DuplicateAggregate` are a conflict (409 over HTTP), `AggregateNotFound` declares not found
(404), and the rest — `InvalidCriteria` and `ContractViolation` included — is the domain
refusing the request (422). An error of a project's own declares another kind with
`failure_kind = FailureKind...` on its class.
"""

from sincpro_framework.common.failures import FailureKind


class DomainError(Exception):
    """Base for everything the vocabulary raises. Catch this to catch all of it."""


class InvalidCriteria(DomainError):
    """What was asked cannot be answered: an ordering that would lose rows, a cursor from
    another ordering, a field that is not there."""


class ContractViolation(DomainError):
    """The API is being used against its contract: a class that was never mapped, a page asked
    to fold itself, a lock outside a transaction, a typed publish on a queue that cannot
    answer."""


class StaleAggregate(DomainError):
    """A save found a newer version of the aggregate than the one it was handed.

    Somebody else wrote between this caller's read and its write. The caller's fix is to
    read again and decide over the current state, never to retry the same write.
    """


class DuplicateAggregate(DomainError):
    """A save collided with a value the storage keeps unique — an identity, a fingerprint.

    Reported as the vocabulary's own word so a use case can resolve the race it was written
    for, without importing the driver's exception to do it.
    """


class ConstraintViolation(ContractViolation):
    """The database refused a write a rule of its own forbids: a reference to a row that does
    not exist, an empty value where one is required, a check.

    Unlike a race, running it again changes nothing — the write itself is wrong. The engine's
    message, naming the constraint, is carried in this one.
    """


class TransactionConflict(DomainError):
    """The transaction lost to another one running at the same time: a serialization failure,
    a deadlock, a row lock it would not wait for.

    The one failure that is cured by running the whole unit of work again on a fresh read —
    which is what `Repository.retrying` does by default.
    """


class TimedOut(DomainError):
    """The engine stopped at a bound the unit of work set: a row lock it was told not to wait
    for (`nowait`), or a statement past `timeout`.

    Not retried by default — the bound was the caller's choice, and running into it again at
    once is what it was set to avoid. A worker that wants the next free row asks `skip_locked`.
    """


class RelationNotResolved(ContractViolation):
    """A relation was read that nobody asked for, outside a unit of work.

    Inside `context()` a relation resolves on first touch. Outside, the record is detached and
    a loop over two hundred rows would be two hundred queries hidden in an attribute access;
    name the relation in the criteria's specification instead.
    """


class WriteInPreview(ContractViolation):
    """A write was attempted inside `previewing()`: a save, a removal, a bulk write or a number
    taken while answering a form's question. A preview stores nothing; the write belongs to the
    Command that saves.
    """


class AggregateNotFound(DomainError):
    """A read named one record by its identity and there is none: never stored, archived, or
    outside the scope the repository was narrowed to.

    What `Get` answers instead of an empty record, so every wire says «not found» its own way.
    """

    failure_kind = FailureKind.NOT_FOUND
