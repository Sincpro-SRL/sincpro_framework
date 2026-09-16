"""What the vocabulary refuses, in its own words.

Four of them, because a caller only ever needs to tell four things apart: what it asked for
cannot be answered, the contract is being used wrong, or a write lost a race — against a newer
version of the same aggregate, or against another aggregate already holding a unique value.

They inherit from `Exception` and nothing else. Whoever exposes them decides what each one means
over the wire; an HTTP entrypoint maps `InvalidCriteria` to 400, `ContractViolation` to 422 and
the two conflicts to 409.
"""


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


class RelationNotResolved(ContractViolation):
    """A relation was read that nobody asked for, outside a unit of work.

    Inside `context()` a relation resolves on first touch. Outside, the record is detached and
    a loop over two hundred rows would be two hundred queries hidden in an attribute access;
    name the relation in the criteria's specification instead.
    """
