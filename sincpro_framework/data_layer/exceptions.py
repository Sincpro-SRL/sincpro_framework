"""The errors the data layer raises and catches: those of the repositories it implements, of the
migrations its engines run, and the framework's own."""

from sincpro_framework.data_layer.migrations.domain.exceptions import MigrationRefused
from sincpro_framework.ddd.exceptions import (
    ConstraintViolation,
    DomainError,
    DuplicateAggregate,
    InvalidCriteria,
    RelationNotResolved,
    StaleAggregate,
    TimedOut,
    TransactionConflict,
)
from sincpro_framework.exceptions import ProgrammingError
