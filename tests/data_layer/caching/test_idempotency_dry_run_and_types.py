"""Idempotency and the dry run: a dry run is never claimed — it writes nothing, so there is
nothing to run once — and idempotency declared once, for a type of Command, reaches every
handler of it."""

import time
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    DryRunnable,
    Feature,
    Preview,
    UseFramework,
    idempotency,
)
from sincpro_framework.context import KeyValueContexts
from sincpro_framework.data_layer.caching import (
    Idempotency,
    IdempotencyOutcome,
    IdempotencyPolicy,
    JsonCodec,
    KeyValueRecords,
)
from sincpro_framework.data_layer.store import (
    InMemoryKeyValue,
    RedisKeyValue,
    StoreUnavailable,
)

TWO_MINUTES = timedelta(minutes=2)
ANA = {"tenant_id": "acme", "user_id": "ana"}


class WatchedKeyValue(InMemoryKeyValue):
    """A store that remembers every key an idempotency record was written under."""

    def __init__(self) -> None:
        super().__init__()
        self.written: list[str] = []

    def _watched(self, key: str) -> None:
        if key.startswith("idempotency:"):
            self.written.append(key)

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self._watched(key)
        super().set(key, value, ttl)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        self._watched(key)
        return super().add(key, value, ttl)

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        self._watched(key)
        return super().replace(key, expected, value, ttl)


class WriteCommand(DryRunnable):
    dry_run: bool = True  # previewed unless the caller says otherwise


class CommandUpdateOpportunity(WriteCommand):
    opportunity_id: int
    stage: str


class ResponseUpdated(DataTransferObject):
    opportunity_id: int


class CommandConvertLead(WriteCommand):
    lead_id: int


class ResponseConverted(DataTransferObject):
    opportunity_id: int


def write_bus(
    name: str, store: Any, written: list[str], declared: bool = True
) -> UseFramework:
    """A bus whose writes answer their dry run in `execute` — `written` is what reached the
    customer's system; `declared` puts `@bus.idempotency` on the Feature itself."""
    bus = UseFramework(name, log_after_execution=False)
    bus.store_context_in(KeyValueContexts(store))

    class UpdateOpportunity(Feature):
        def execute(self, dto: CommandUpdateOpportunity) -> ResponseUpdated | Preview:
            if dto.dry_run:
                return Preview(text=f"Opportunity {dto.opportunity_id} to {dto.stage}")
            written.append(f"{dto.opportunity_id} {dto.stage}")
            return ResponseUpdated(opportunity_id=dto.opportunity_id)

    if declared:
        UpdateOpportunity = bus.idempotency(expires_after=TWO_MINUTES)(UpdateOpportunity)
    bus.feature(CommandUpdateOpportunity)(UpdateOpportunity)
    return bus


# --- F1: a dry run is never claimed -------------------------------------------------------------


def test_a_dry_run_answered_in_execute_writes_nothing_to_the_store_and_the_run_is_stored():
    store, written = WatchedKeyValue(), []
    bus = write_bus("crm-dry-once", store, written)

    with bus.context(ANA):
        shown = bus(CommandUpdateOpportunity(opportunity_id=7, stage="won"))
        assert isinstance(shown, Preview) and store.written == []

        done = bus(CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False))
        assert bus.idempotency.completed(
            CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False)
        )

    assert done == ResponseUpdated(opportunity_id=7) and written == ["7 won"]
    assert store.written != []


def test_a_dry_run_is_never_in_outcomes():
    bus = write_bus("crm-dry-outcomes", WatchedKeyValue(), [])

    with bus.context(ANA), bus.idempotency.outcomes() as seen:
        bus(CommandUpdateOpportunity(opportunity_id=7, stage="won"))

    assert seen == []


def test_with_the_store_down_a_dry_run_answers_and_the_run_does_not():
    down = RedisKeyValue.from_url("redis://127.0.0.1:1/0", prefix="dry-down")
    written: list[str] = []
    bus = write_bus("crm-dry-down", down, written)

    with bus.context(ANA):
        shown = bus(CommandUpdateOpportunity(opportunity_id=7, stage="won"))
        with pytest.raises(StoreUnavailable):
            bus(CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False))

    assert isinstance(shown, Preview) and written == []


def test_an_application_service_and_exactly_once_let_a_dry_run_through_too():
    store, ran = WatchedKeyValue(), []
    bus = UseFramework("crm-dry-exactly", log_after_execution=False)
    bus.store_context_in(KeyValueContexts(store))

    @bus.app_service(CommandConvertLead)
    @bus.idempotency.exactly_once(KeyValueRecords(store), expires_after=TWO_MINUTES)
    class ConvertLead(ApplicationService):
        def execute(self, dto: CommandConvertLead) -> ResponseConverted | Preview:
            if dto.dry_run:
                return Preview(text=f"Lead {dto.lead_id} to an opportunity")
            ran.append(dto.lead_id)
            return ResponseConverted(opportunity_id=dto.lead_id * 10)

    with bus.context(ANA):
        bus(CommandConvertLead(lead_id=3))
        assert store.written == []
        bus(CommandConvertLead(lead_id=3, dry_run=False))
        bus(CommandConvertLead(lead_id=3, dry_run=False))

    assert ran == [3]


# --- F2: declared once, for a type of Command -----------------------------------------------------


def typed_bus(name: str, written: list[str]) -> UseFramework:
    """A bus whose idempotency is declared once for every `WriteCommand`, and whose Features
    declare none — one registered before the declaration, one after."""
    bus = write_bus(name, WatchedKeyValue(), written, declared=False)
    bus.idempotency.declare_for(
        WriteCommand,
        expires_after=timedelta(seconds=120),
        in_progress_for=timedelta(seconds=60),
        wait_for_completion=timedelta(seconds=25),
    )

    @bus.app_service(CommandConvertLead)
    class ConvertLead(ApplicationService):
        def execute(self, dto: CommandConvertLead) -> ResponseConverted | Preview:
            if dto.dry_run:
                return Preview(text=f"Lead {dto.lead_id}")
            written.append(f"lead {dto.lead_id}")
            return ResponseConverted(opportunity_id=dto.lead_id * 10)

    return bus


def test_a_type_declared_once_runs_every_handler_of_it_once():
    written: list[str] = []
    bus = typed_bus("crm-typed", written)

    with bus.context(ANA), bus.idempotency.outcomes() as seen:
        for _ in range(2):
            bus(CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False))
            bus(CommandConvertLead(lead_id=3, dry_run=False))
        bus(CommandUpdateOpportunity(opportunity_id=7, stage="won"))  # a dry run

    assert written == ["7 won", "lead 3"]
    assert seen == [
        IdempotencyOutcome.CLAIMED,
        IdempotencyOutcome.CLAIMED,
        IdempotencyOutcome.REPLAYED,
        IdempotencyOutcome.REPLAYED,
    ]


def test_a_type_declaration_is_reported_and_answers_completed():
    bus = typed_bus("crm-typed-reported", [])
    bus.build_root_bus()
    handlers = {**bus.feature_handlers(), **bus.app_service_handlers()}

    assert all(Idempotency.declared_by(handler) for handler in handlers.values())
    policy = next(iter(bus.idempotency.policies().values()))
    assert policy.wait_for_completion == timedelta(seconds=25)

    command = CommandConvertLead(lead_id=3, dry_run=False)
    with bus.context(ANA):
        assert not bus.idempotency.completed(command)
        bus(command)
        assert bus.idempotency.completed(command)


def test_a_class_that_declares_its_own_keeps_it():
    bus = write_bus("crm-own-wins", WatchedKeyValue(), [], declared=True)
    bus.idempotency.declare_for(WriteCommand, expires_after=timedelta(seconds=5))

    bus.build_root_bus()

    [policy] = bus.idempotency.policies().values()
    assert policy.expires_after == TWO_MINUTES


def test_the_nearer_type_wins():
    class CommandCloseOpportunity(CommandUpdateOpportunity): ...

    bus = UseFramework("crm-nearer", log_after_execution=False)

    @bus.feature(CommandCloseOpportunity)
    class CloseOpportunity(Feature):
        def execute(self, dto: CommandCloseOpportunity) -> ResponseUpdated:
            return ResponseUpdated(opportunity_id=dto.opportunity_id)

    bus.idempotency.declare_for(WriteCommand, expires_after=timedelta(seconds=5))
    bus.idempotency.declare_for(CommandUpdateOpportunity, expires_after=timedelta(seconds=9))
    bus.build_root_bus()

    [policy] = bus.idempotency.policies().values()
    assert policy.expires_after == timedelta(seconds=9)


def test_a_new_generation_and_a_fresh_bus_take_it_too():
    written: list[str] = []
    bus = typed_bus("crm-generations", written)
    bus.build_root_bus()

    class CommandArchiveLead(WriteCommand):
        lead_id: int

    @bus.feature(CommandArchiveLead)
    class ArchiveLead(Feature):  # registered after the build: the next generation
        def execute(self, dto: CommandArchiveLead) -> ResponseConverted:
            written.append(f"archive {dto.lead_id}")
            return ResponseConverted(opportunity_id=dto.lead_id)

    class CommandReopenLead(WriteCommand):
        lead_id: int

    fresh = bus.fresh()

    @fresh.feature(CommandReopenLead)
    class ReopenLead(Feature):  # only on the fresh bus
        def execute(self, dto: CommandReopenLead) -> ResponseConverted:
            written.append(f"reopen {dto.lead_id}")
            return ResponseConverted(opportunity_id=dto.lead_id)

    with bus.context(ANA):
        bus(CommandArchiveLead(lead_id=1, dry_run=False))
        bus(CommandArchiveLead(lead_id=1, dry_run=False))
    with fresh.context(ANA):
        fresh(CommandReopenLead(lead_id=2, dry_run=False))
        fresh(CommandReopenLead(lead_id=2, dry_run=False))

    assert written == ["archive 1", "reopen 2"]


def test_the_service_declaration_reaches_every_bus(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(idempotency, "declared_for", [])
    idempotency.declare_for(WriteCommand, expires_after=TWO_MINUTES)
    written: list[str] = []
    bus = write_bus("crm-service-wide", WatchedKeyValue(), written, declared=False)

    with bus.context(ANA):
        bus(CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False))
        bus(CommandUpdateOpportunity(opportunity_id=7, stage="won", dry_run=False))

    assert written == ["7 won"]


# --- completed_key: a key run by hand --------------------------------------------------------------


def test_a_key_run_by_hand_says_whether_it_completed():
    bus = UseFramework("crm-by-hand", log_after_execution=False)
    bus.store_context_in(KeyValueContexts(InMemoryKeyValue()))
    policy = IdempotencyPolicy(expires_after=timedelta(milliseconds=50))
    codec = JsonCodec(ResponseUpdated)

    bus.idempotency.run(
        ("update", 7), lambda: ResponseUpdated(opportunity_id=7), policy, codec
    )

    assert bus.idempotency.completed_key(("update", 7)) is True
    assert bus.idempotency.completed_key(("update", 8)) is False
    time.sleep(0.1)
    assert bus.idempotency.completed_key(("update", 7)) is False  # expired


def test_the_service_facade_answers_it_too():
    policy = IdempotencyPolicy(expires_after=TWO_MINUTES)
    idempotency.run(("by-hand", "x-1"), lambda: 1, policy, JsonCodec(int))

    assert idempotency.completed_key(("by-hand", "x-1")) is True
    assert idempotency.completed_key(("by-hand", "x-2")) is False
