"""The compiled models of a process: one per class, compiled once, the same object afterwards.

    models.of(Sale)      compiles the first time, answers the same model after
    models.forget(Sale)  the next read compiles it again — a class whose declaration changed
    models.forget()      every model, compiled again when next read
    models.learn(Sale, facts)   what the store that keeps Sale last said of it — what is published

Context: a model is immutable, so readers hold it without a lock; only compiling takes one, so two
threads asking for the same class at once compile it once. What a store knows is held apart from
the model: a mapping that changes a class forgets its model, never what its store said. A model
compiled again with another fingerprint — an extension installed, a relation added — is said once,
in the log, naming the class and both versions: what a client that holds the old one has to hear.
"""

import dataclasses
import threading

from sincpro_framework.ddd.metamodel.domain.facts import StoreFacts
from sincpro_framework.ddd.metamodel.domain.model import Model
from sincpro_framework.ddd.metamodel.services.compiler import compiled, declaration_of
from sincpro_framework.sincpro_logger import logger


class Models:
    """The models compiled in this process, by class."""

    def __init__(self) -> None:
        """Nothing compiled yet."""
        self._held: dict[type, Model] = {}
        self._facts: dict[type, StoreFacts] = {}
        self._versions: dict[type, str] = {}
        self._lock = threading.Lock()

    def of(self, declared: type) -> Model:
        """The model of `declared`, compiled the first time it is asked for.

        1. A model already held is answered, with no lock.
        2. Otherwise, under the lock, asked again — another thread may have compiled it — and
           compiled from the class's declaration.
        3. A class compiled before under another fingerprint changed: said once, with both
           versions.
        Final: the model, held for every later read.
        """
        held = self._held.get(declared)
        if held is not None:
            return held
        with self._lock:
            held = self._held.get(declared)
            if held is None:
                held = compiled(declaration_of(declared))
                self._held[declared] = held
                before = self._versions.get(declared)
                if before is not None and before != held.fingerprint:
                    logger.info(
                        f"metamodel: {held.name} changed, version {before[:12]} -> "
                        f"{held.fingerprint[:12]}"
                    )
                self._versions[declared] = held.fingerprint
            return held

    def forget(self, *declared: type) -> None:
        """Let go of the models of `declared` — of every model when none is named; the next read
        compiles each again. What a mapping that adds a relation to a class, or an extension that
        overlays one, calls once it changed what the class declares."""
        with self._lock:
            if not declared:
                self._held.clear()
            for one in declared:
                self._held.pop(one, None)

    def learn(self, declared: type, facts: StoreFacts) -> None:
        """Hold what a store said of `declared` — the last one said wins —, with nothing that
        reaches back into the store: what the published contract is rendered with."""
        self._facts[declared] = dataclasses.replace(facts, related=None)

    def facts_of(self, declared: type) -> StoreFacts | None:
        """What the last store that described `declared` said of it; `None` when none did."""
        return self._facts.get(declared)


models = Models()
"""The models of this process."""
