"""What a project defines while it runs, without a deploy: use cases stored as source and
loaded onto the bus, and the tools a test suite runs the framework with.

    registry = BusRegistry(billing, InMemoryUseCases())   # runtime_use_cases
    registry.put(RuntimeUseCase(name="quote", source=source))

    with override_dependencies(billing, ledger=FakeLedger()):   # testing
        billing(CommandQuote(amount=100))
"""
