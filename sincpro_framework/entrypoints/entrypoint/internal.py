"""What no entrypoint publishes: a use case marked `internal` runs in the process — another use
case, a cron, a test calls it — and is on no wire, whatever the gateway includes.

    @billing.feature(CommandReconcileLedger)
    @internal
    class ReconcileLedger(Feature): ...

Context: decided once on the use case rather than on every gateway's `exclude`, so a gateway added
later — REST today, another tomorrow — never publishes it by forgetting. The mark is kept here,
never written onto the class; the handler or its Command may carry it.
"""

from weakref import WeakSet

_internal: WeakSet[type] = WeakSet()


def internal[T: type](cls: T) -> T:
    """Keep this Feature, ApplicationService or Command off every entrypoint."""
    _internal.add(cls)
    return cls


def is_internal(*classes: type | None) -> bool:
    """Whether any of `classes` — the Command, its handler — was marked `internal`; a `None`
    (no handler found) counts as not marked.

        is_internal(CommandReconcileLedger, ReconcileLedger)  ->  True
        is_internal(CommandIssueInvoice, None)                ->  False
    """
    return any(one is not None and one in _internal for one in classes)
