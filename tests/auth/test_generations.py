"""A guarded bus keeps its guard on every generation `fresh()` makes of it — the one the runtime
registry swaps in when a use case stored as source is saved, and the one after that.

The incidents: every call of a generation failing because the guard asked the first bus for a
handler it never had; a stored use case's own declaration ignored for the one it replaces; and a
new version of a stored use case inheriting — or refused for — what the previous version said.
"""

import pytest

from sincpro_framework.auth import PermissionDenied, Unauthenticated, as_system
from sincpro_framework.runtime.runtime_use_cases import (
    BusRegistry,
    InMemoryUseCases,
    RuntimeUseCase,
)
from sincpro_framework.runtime.testing import granting
from tests.auth.guarded_billing import (
    PUBLIC_TAX,
    BillingPermission,
    CommandComputeTax,
    ResponseComputeTax,
    auth,
    billing,
    quote,
)

QUOTE = "guarded-billing.CommandQuote"


def _registry(*use_cases: RuntimeUseCase) -> BusRegistry:
    store = InMemoryUseCases()
    for use_case in use_cases:
        store.save(use_case)
    return BusRegistry(billing, store)


def test_a_stored_use_case_is_guarded_by_what_it_declares() -> None:
    registry = _registry(
        RuntimeUseCase(name="quote", source=quote("@auth.requires(BillingPermission.QUOTE)"))
    )
    with granting(BillingPermission.QUOTE):
        assert registry.execute(QUOTE, {"amount": 2}).total == 4
    with granting(BillingPermission.TAX), pytest.raises(PermissionDenied):
        registry.execute(QUOTE, {"amount": 2})
    with as_system("test"):
        assert registry.execute(QUOTE, {"amount": 2}).total == 4


def test_the_use_cases_in_code_stay_guarded_on_a_new_generation() -> None:
    registry = _registry(RuntimeUseCase(name="quote", source=quote("")))
    with pytest.raises(Unauthenticated):
        registry.current(CommandComputeTax(amount=100), ResponseComputeTax)
    with granting(BillingPermission.TAX):
        answer = registry.current(CommandComputeTax(amount=100), ResponseComputeTax)
    assert answer is not None and answer.tax == 10


def test_a_stored_replacement_holds_to_its_own_declaration() -> None:
    registry = _registry(
        RuntimeUseCase(
            name="tax", source=PUBLIC_TAX, replaces="tests.auth.guarded_billing.ComputeTax"
        )
    )
    answer = registry.current(CommandComputeTax(amount=100), ResponseComputeTax)
    assert answer is not None and answer.tax == 0


def test_a_new_version_declares_anew_and_never_adds_to_the_last() -> None:
    registry = _registry(
        RuntimeUseCase(name="quote", source=quote("@auth.requires(BillingPermission.QUOTE)"))
    )
    registry.put(
        RuntimeUseCase(
            name="quote",
            source=quote("@auth.requires(BillingPermission.QUOTE_V2)"),
            version=2,
        )
    )
    with granting(BillingPermission.QUOTE_V2):
        assert registry.execute(QUOTE, {"amount": 1}).total == 2
    registry.put(RuntimeUseCase(name="quote", source=quote("@auth.public"), version=3))
    assert registry.execute(QUOTE, {"amount": 1}).total == 2


def test_checking_a_draft_leaves_the_version_in_force_as_it_was() -> None:
    registry = _registry(
        RuntimeUseCase(name="quote", source=quote("@auth.requires(BillingPermission.QUOTE)"))
    )
    registry.check(
        RuntimeUseCase(
            name="quote",
            source=quote("@auth.requires(BillingPermission.QUOTE_V2)"),
            version=2,
        )
    )
    with granting(BillingPermission.QUOTE):
        assert registry.execute(QUOTE, {"amount": 1}).total == 2


def test_verify_counts_what_runs_on_a_generation() -> None:
    registry = _registry(
        RuntimeUseCase(name="quote", source=quote("@auth.requires(BillingPermission.QUOTE)"))
    )
    registry.reload()
    assert not [one for one in auth.verify() if "Quote" in one]
