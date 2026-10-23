"""The errors the gRPC wire raises and answers with a status of their own."""

from sincpro_framework.auth.domain.exceptions import AuthError, Unauthenticated
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.exceptions import FailureKind, ProgrammingError
