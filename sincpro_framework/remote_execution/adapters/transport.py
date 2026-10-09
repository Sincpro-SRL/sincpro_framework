"""What every transport answers: one execution, answered as the use case answers it.

    Transport.execute("billing", CommandIssueInvoice(...), ResponseIssueInvoice, request_context)

Context: the contract `transport_for` holds the transports by. Only that facade picks one; the
bus calls what it hands back and never names this `Protocol`, so it lives with the adapters
that implement it, not in `domain/`.
"""

from collections.abc import Mapping
from typing import Any, Protocol


class Transport(Protocol):
    def execute(
        self, context: str, dto: Any, response: Any, request: Mapping[str, Any]
    ) -> Any: ...
