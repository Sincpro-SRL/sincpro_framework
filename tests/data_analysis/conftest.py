"""Invoice lines in a memory repository that counts every time it is asked — so a test says how
many reads reached the store."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from sincpro_framework.ddd import Criteria, Entity, MemoryRepository


@dataclass
class Line(Entity):
    journal: str = ""
    state: str = ""
    posted_at: date = date(2026, 1, 1)
    amount: Decimal = Decimal("0")
    quantity: int = 0


class CountingRepository(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> Any:
        self.reads += 1
        return super().search(target, criteria, for_update, skip_locked)


def lines(count: int) -> list[Line]:
    return [
        Line(
            id=f"l{n:05d}",
            journal=["SAL", "PUR", "BNK"][n % 3],
            state="posted" if n % 4 else "draft",
            posted_at=date(2026, 1 + n % 12, 1 + n % 28),
            amount=Decimal(n % 997) / 10,
            quantity=n % 7,
        )
        for n in range(count)
    ]


@pytest.fixture
def repository() -> CountingRepository:
    repository = CountingRepository()
    for line in lines(1_000):
        repository.save(line)
    repository.reads = 0
    return repository


POSTED = {"field": "state", "operator": "=", "value": "posted"}


def criteria(limit: int = 100, where: dict | None = None) -> Criteria:
    return Criteria.model_validate(
        {"where": where or POSTED, "order": [{"field": "id"}], "pagination": {"limit": limit}}
    )
