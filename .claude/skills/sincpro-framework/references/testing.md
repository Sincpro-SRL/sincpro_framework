# Testing — through the bus, with doubled adapters

A test earns its place when its failure would be an incident: a duplicate created, a partial write
from an ApplicationService, an adapter error that becomes a generic 500, a Feature that cannot see
its dependency. A test that only proves Pydantic accepted a field or that `@bus.feature` registered
is noise.

## Exercise the use case the way production does

```python
# ✅ the same path every entrypoint uses: span, error report, context, interceptors, auth
result = billing(CommandIssueInvoice(customer_id="c-1", amount=100), ResponseIssueInvoice)

# ❌ skips the bus and has no injected dependencies
IssueInvoice().execute(CommandIssueInvoice(...))
```

## Double an adapter for one test

`override_dependencies` swaps a registered dependency on every handler of the bus for the length of
the block, and refuses a name that was never registered (a typo would otherwise leave the real
adapter in place):

```python
from sincpro_framework.runtime.testing import override_dependencies


def test_issue_invoice_rejects_unknown_customer():
    with override_dependencies(billing, customer_lookup=FakeCustomerLookup(known=[])):
        with pytest.raises(CustomerNotFound):
            billing(CommandIssueInvoice(customer_id="c-404", amount=100), ResponseIssueInvoice)
```

Replace I/O at the adapter, never by subclassing the Feature.

## Checks every project keeps in its suite

```python
from sincpro_framework.runtime.testing import import_cycles, layer_violations, unregistered_dependencies


def test_wiring_is_whole():
    assert unregistered_dependencies(billing) == {}    # every DependencyContextType name registered


def test_layers():
    assert layer_violations("my_service") == []         # rules in context-boundaries.md


def test_no_import_cycles():
    assert import_cycles("my_service") == []
```

`layer_violations` reads the source without importing it. A project that does not follow one of its
rules passes that rule's name in `ignore=`.

## What to protect

1. The business rule the Feature exists to enforce (same input twice returns the existing record;
   an invalid value is refused before the external call).
2. ApplicationService atomicity: a failure before the first write leaves no partial state. Call the
   ApplicationService's Command, not the children.
3. Adapter error mapping: a provider's 4xx/5xx becomes the project's `DomainError` with the
   provider's message, not a traceback.
4. Dependency wiring (`unregistered_dependencies`).

## What not to test

- That a decorator registered the Command, or framework behaviour in general.
- Trivial getters, `model_dump()`, private helper call order.
- Transport (routes, tool lists) unless the change is the transport — then snapshot
  `gateway.manifest()` (`sincpro-framework-entrypoints`).

## Other doubles in `sincpro_framework.runtime.testing`

`RecordingQueue` (events published, `sincpro-framework-domain-events`), `as_identity` /
`as_system` / `granting` / `RecordingProvider` (auth, `sincpro-framework-auth`), `ManualClock`
(crons, `sincpro-framework-operations`), and the `*Contract` suites an adapter proves itself
against (`KeyValueStoreContract`, `IdempotencyRecordsContract`, `AuthProviderContract`).

Layout: `tests/` mirroring the package closely enough to find a test
(`tests/domains/<context>/<layer>/test_*.py`). Runner: `make test`.
