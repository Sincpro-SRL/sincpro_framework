"""The port a transport implements: one execution, answered as the use case answers it.

Transport.execute("billing", CommandIssueInvoice(...), ResponseIssueInvoice, request_context)
"""

from collections.abc import Mapping
from typing import Any, Protocol


class Transport(Protocol):
    def execute(
        self, context: str, dto: Any, response: Any, request: Mapping[str, Any]
    ) -> Any: ...
