"""What a store has to answer for the behaviours of this folder to run on it."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any


class World:
    """Inherit the behaviours you want, answer `make_world`, and run with pytest.

    `make_world` returns the classes of `classes.make_classes()` and a store holding nothing, with
    the extensions declared the way that store declares them (`map_aggregates` for a database,
    `MemoryRepository.extended` for the double). `delegated=True` asks for answers that nest each
    extension under its key.
    """

    def make_world(self, delegated: bool = False) -> tuple[SimpleNamespace, Any]:
        raise NotImplementedError("answer the classes of make_classes() and an empty store")

    def populated(self, delegated: bool = False) -> tuple[SimpleNamespace, Any]:
        k, store = self.make_world(delegated)
        store.save(k.Move(id="A", number="F-A", total=Decimal("1")))
        store.save(k.MoveDelivery(id="B", number="F-B", total=Decimal("2"), route="R7"))
        store.save(
            k.MoveTracking(
                id="C", number="F-C", total=Decimal("3"), route="R7", carrier="DHL"
            )
        )
        store.save(k.MoveFiscal(id="E", number="F-E", total=Decimal("5"), cufd="C1"))
        return k, store
