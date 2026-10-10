"""What a registered class is known by, on every wire and in every registry and signal."""


def registered_name(cls: type, context: str) -> str:
    """What a registered class is known by on every wire, in every registry and every signal.

    >>> registered_name(CommandIssueInvoice, "billing")
    'billing.CommandIssueInvoice'
    >>> registered_name(InvoicePaid, "billing")     # an event answers its own `name`
    'billing.invoice.paid'

    A class that knows its own identity answers it (`identity_in`, which `DomainEvent` defines);
    any other is known by its bounded context and its class name, so moving it to another module
    never changes it and two contexts with a `CommandCreate` each keep their own.
    """
    own = getattr(cls, "identity_in", None)
    if callable(own):
        return str(own(context))
    return f"{context}.{cls.__name__}" if context else cls.__name__


def local_name(name: str, context: str) -> str:
    """`name` as the context `context` knows it alone: its identity without the context."""
    return name.removeprefix(f"{context}.") if context else name
