"""What the vocabulary refuses, in its own words — each on the base that says who caused it.

    DomainError           a rule of the business says no                   (domain, 422)
      StaleAggregate        a write lost to a newer version                  (conflict, 409)
      DuplicateAggregate    a write collided with a unique value             (conflict, 409)
      TransactionConflict   the transaction lost to another one              (conflict, 409)
      ConstraintViolation   the database refused the write by a rule of its own
    InvalidCriteria       the caller asked what cannot be answered          (invalid, 400)
    AggregateNotFound     the record the caller named is not there          (not found, 404)
    TimedOut              the engine stopped at a bound it was given        (unavailable, 503)
    RelationNotResolved   the code read a relation nobody asked for         (internal, 500)

A stale version and a lost transaction are the ones worth running again. A project's own error
declares another kind with `failure_kind = FailureKind...` on its class.
"""

from sincpro_framework.exceptions import (
    ClientError,
    FailureKind,
    FrameworkError,
    ProgrammingError,
    ServiceUnavailableError,
)


class DomainError(FrameworkError):
    """A rule of the business says no — the request was well formed, and the answer is a refusal.
    Its message is the answer, written for whoever asked, and told to them."""

    failure_kind = FailureKind.DOMAIN


class InvalidCriteria(ClientError):
    """What was asked cannot be answered: an ordering that would lose rows, a cursor from
    another ordering, a field that is not there."""


class StaleAggregate(DomainError):
    """A save found a newer version of the aggregate than the one it was handed.

    Somebody else wrote between this caller's read and its write. The caller's fix is to
    read again and decide over the current state, never to retry the same write.
    """

    failure_kind = FailureKind.CONFLICT


class DuplicateAggregate(DomainError):
    """A save collided with a value the storage keeps unique — an identity, a fingerprint.

    Reported as the vocabulary's own word so a use case can resolve the race it was written
    for, without importing the driver's exception to do it.
    """

    failure_kind = FailureKind.CONFLICT


class ConstraintViolation(DomainError):
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

    failure_kind = FailureKind.CONFLICT


class TimedOut(ServiceUnavailableError):
    """The engine stopped at a bound the unit of work set: a row lock it was told not to wait
    for (`nowait`), or a statement past `timeout`.

    Not retried by `Repository.retrying` — the bound was the caller's choice, and running into it
    again at once is what it was set to avoid. A worker that wants the next free row asks
    `skip_locked`.
    """


class RelationNotResolved(ProgrammingError):
    """A relation was read that nobody asked for, outside a unit of work.

    Inside `context()` a relation resolves on first touch. Outside, the record is detached and
    a loop over two hundred rows would be two hundred queries hidden in an attribute access;
    name the relation in the criteria's specification instead.
    """


class AggregateNotFound(ClientError):
    """A read named one record by its identity and there is none: never stored, archived, or
    outside the scope the repository was narrowed to.

    What `Get` answers instead of an empty record, so every wire says «not found» its own way.
    """

    failure_kind = FailureKind.NOT_FOUND
