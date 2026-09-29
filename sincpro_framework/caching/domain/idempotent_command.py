"""`IdempotentCommand`: the Command says what identifies one request — with a plain method.

    class CommandIssueInvoice(DataTransferObject):
        request_id: str
        total: int

        def idempotency_key(self) -> str:
            return self.request_id

Context: the key belongs to the Command, not to whoever runs it. A method is plain Python: a
rename moves it, a type checker checks it, and nothing about the DTO changes — no field named in
a string, no annotation to learn. The fields that are not the key still count: a key reused with
a different `total` is `KeyReused`. A Command without the method is keyed by every field.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IdempotentCommand(Protocol):
    def idempotency_key(self) -> Any:
        """What identifies one request — a value, or a tuple of them."""
        ...


def request_key_of(command: Any) -> Any:
    """What identifies `command`: its own `idempotency_key()`, or the whole Command."""
    return command.idempotency_key() if isinstance(command, IdempotentCommand) else command
