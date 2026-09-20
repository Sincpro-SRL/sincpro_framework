"""A whole shop per test: fresh databases, nothing shared between tests."""

from collections.abc import Callable

import pytest

from .wiring import Shop, distributed, monolith, paired


@pytest.fixture
def one_database() -> Shop:
    """Case 1: every context in one database."""
    return monolith()


@pytest.fixture
def two_databases() -> Shop:
    """Case 2: orders+inventory share one, billing+notifications the other."""
    return paired()


@pytest.fixture
def four_databases() -> Shop:
    """Case 3: a database each."""
    return distributed()


@pytest.fixture
def shops() -> dict[str, Callable[..., Shop]]:
    """All three, for a test that is about what does *not* change between them."""
    return {"monolith": monolith, "paired": paired, "distributed": distributed}
