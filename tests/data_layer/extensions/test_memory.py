"""The memory store, held to the behaviours of this folder: the double agrees with the engine."""

from sincpro_framework.data_layer.repositories.memory_repository import MemoryRepository

from .answers import ExtendedAnswers
from .classes import make_classes
from .editing import DelegatedEdits, ExtendedEdits
from .reading import ExtendedReads
from .writing import ExtendedWrites


class MemoryWorld:
    def make_world(self, delegated: bool = False):
        k = make_classes()
        store = MemoryRepository().extended(
            k.Move, k.MoveDelivery, k.MoveTracking, k.MoveFiscal, delegated=delegated
        )
        return k, store


class TestReads(MemoryWorld, ExtendedReads):
    pass


class TestWrites(MemoryWorld, ExtendedWrites):
    pass


class TestAnswers(MemoryWorld, ExtendedAnswers):
    pass


class TestEdits(MemoryWorld, ExtendedEdits):
    pass


class TestDelegatedEdits(MemoryWorld, DelegatedEdits):
    pass
