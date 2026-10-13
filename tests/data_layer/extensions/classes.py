"""The entity and its extensions every behaviour in this folder is written over: plain dataclasses."""

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from sincpro_framework.ddd import Entity


def make_classes() -> SimpleNamespace:
    """Fresh for each world: a chain (delivery, then tracking) and a sibling of delivery (fiscal).

    Move ← MoveDelivery(route) ← MoveTracking(carrier)
    Move ← MoveFiscal(cufd)
    """

    @dataclass
    class Move(Entity):
        number: str = ""
        total: Decimal = Decimal("0")

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    @dataclass
    class MoveTracking(MoveDelivery):
        carrier: str = ""

    @dataclass
    class MoveFiscal(Move):
        cufd: str = ""

    return SimpleNamespace(
        Move=Move, MoveDelivery=MoveDelivery, MoveTracking=MoveTracking, MoveFiscal=MoveFiscal
    )
