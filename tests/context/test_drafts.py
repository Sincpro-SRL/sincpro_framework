"""Drafts kept between requests in the context's store: optimistic, with a TTL, the same on both
stores.

Every case runs against `InMemoryContexts` and against `KeyValueContexts` over a `KeyValueStore`
(in memory here, Redis when `fakeredis` is installed), judged by one clock. A draft is an entry of
the store under `SINCPRO_ENTITY_DRAFT_CONTEXT`, its owner and its subject.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.common.failures import refined_failure_kind
from sincpro_framework.context import (
    ContextStore,
    Draft,
    Drafts,
    Entry,
    InMemoryContexts,
    KeyValueContexts,
    use_context,
)
from sincpro_framework.context.domain import exceptions as context_exceptions
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.ddd import assign
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd import refuse_stale
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.runtime.testing import ManualClock

TTL = timedelta(hours=1)
ANA = {"user_id": "ana"}
KEY = "invoice:7"
ENTRY = "SINCPRO_ENTITY_DRAFT_CONTEXT:tenant_id=,tenant_ids=,user_id=ana:invoice%3A7"
"""The entry of Ana's draft of `KEY`: its namespace, its owner, its subject encoded."""
RAW = f"sincpro:context:entries:{ENTRY}"
"""Where a `KeyValueContexts` keeps it on the key-value store."""


@dataclass
class Drafted:
    drafts: Drafts
    clock: ManualClock
    kv: InMemoryKeyValue | None = None


@pytest.fixture(params=["memory", "key_value"])
def drafted(request) -> Iterator[Drafted]:
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    if request.param == "memory":
        yield Drafted(Drafts(InMemoryContexts(TTL, clock.now), ANA), clock)
    else:
        kv = InMemoryKeyValue(clock.now)
        yield Drafted(
            Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA), clock, kv
        )


# ---------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------


def test_the_first_keep_names_version_zero_and_answers_the_draft_read_gives(drafted: Drafted):
    draft = drafted.drafts.save(KEY, {"discount": "10"}, origin_version=3, expected=0)

    assert draft.version > 0
    assert (draft.origin_version, draft.values) == (3, {"discount": "10"})
    assert drafted.drafts.read(KEY) == draft


def test_a_keep_naming_a_version_that_is_gone_is_a_conflict_carrying_the_current(
    drafted: Drafted,
):
    first = drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    second = drafted.drafts.save(KEY, {"discount": "20"}, 3, expected=first.version)

    with pytest.raises(context_exceptions.DraftConflict) as raised:
        drafted.drafts.save(KEY, {"discount": "30"}, 3, expected=first.version)

    assert second.version > first.version
    assert raised.value.current == second.version
    assert refined_failure_kind(raised.value) is exceptions.FailureKind.CONFLICT
    current = drafted.drafts.read(KEY)
    assert current is not None and current.values == {"discount": "20"}


def test_starting_a_draft_over_a_live_one_is_a_conflict(drafted: Drafted):
    first = drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)

    with pytest.raises(context_exceptions.DraftConflict) as raised:
        drafted.drafts.save(KEY, {"discount": "99"}, 3, expected=0)

    assert raised.value.current == first.version


def test_of_many_keeps_naming_the_same_version_exactly_one_wins(drafted: Drafted):
    first = drafted.drafts.save(KEY, {"n": 0}, 3, expected=0)
    won: list[int] = []
    lost: list[int] = []
    start = threading.Barrier(8)

    def tab(number: int) -> None:
        start.wait()
        try:
            drafted.drafts.save(KEY, {"n": number}, 3, expected=first.version)
            won.append(number)
        except context_exceptions.DraftConflict:
            lost.append(number)

    tabs = [threading.Thread(target=tab, args=(number,)) for number in range(8)]
    for one in tabs:
        one.start()
    for one in tabs:
        one.join()

    assert len(won) == 1 and len(lost) == 7
    current = drafted.drafts.read(KEY)
    assert current is not None and current.version > first.version
    assert current.values == {"n": won[0]}


# ---------------------------------------------------------------------------------------------
# TTL and discard
# ---------------------------------------------------------------------------------------------


def test_an_abandoned_draft_disappears_after_its_ttl(drafted: Drafted):
    drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)

    drafted.clock.advance(minutes=61)

    assert drafted.drafts.read(KEY) is None


def test_each_keep_starts_the_ttl_again(drafted: Drafted):
    first = drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    drafted.clock.advance(minutes=50)
    drafted.drafts.save(KEY, {"discount": "20"}, 3, expected=first.version)
    drafted.clock.advance(minutes=50)

    current = drafted.drafts.read(KEY)
    assert current is not None and current.values == {"discount": "20"}


def test_after_an_expiry_the_next_draft_starts_from_zero_and_never_repeats_a_version(
    drafted: Drafted,
):
    first = drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    drafted.clock.advance(minutes=30)
    second = drafted.drafts.save(KEY, {"discount": "20"}, 3, expected=first.version)
    drafted.clock.advance(minutes=61)

    with pytest.raises(context_exceptions.DraftConflict):
        drafted.drafts.save(KEY, {"discount": "30"}, 3, expected=second.version)
    fresh = drafted.drafts.save(KEY, {"discount": "30"}, 3, expected=0)

    assert fresh.version not in (first.version, second.version)
    assert drafted.drafts.read(KEY) == fresh


def test_a_tab_holding_a_draft_from_before_an_expiry_never_overwrites_the_new_one(
    drafted: Drafted,
):
    """Tab A keeps, goes quiet past the TTL; tab B starts a new draft. A's version is from the
    expired chain and must lose — a version handed out again would let A win in silence."""
    tab_a = drafted.drafts.save(KEY, {"x": "A"}, 3, expected=0)
    drafted.clock.advance(minutes=61)
    tab_b = drafted.drafts.save(KEY, {"x": "B"}, 3, expected=0)

    with pytest.raises(context_exceptions.DraftConflict) as raised:
        drafted.drafts.save(KEY, {"x": "A-stale"}, 3, expected=tab_a.version)

    assert tab_b.version != tab_a.version
    assert raised.value.current == tab_b.version
    current = drafted.drafts.read(KEY)
    assert current is not None and current.values == {"x": "B"}


def test_a_discarded_draft_is_gone_and_a_tab_holding_it_cannot_bring_it_back(
    drafted: Drafted,
):
    first = drafted.drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    second = drafted.drafts.save(KEY, {"discount": "20"}, 3, expected=first.version)

    drafted.drafts.discard(KEY)

    assert drafted.drafts.read(KEY) is None
    with pytest.raises(context_exceptions.DraftConflict):
        drafted.drafts.save(KEY, {"discount": "stale"}, 3, expected=second.version)
    again = drafted.drafts.save(KEY, {"discount": "40"}, 3, expected=0)
    assert again.version > second.version
    assert drafted.drafts.read(KEY) == again


def test_what_a_caller_holds_is_a_copy_of_what_is_kept(drafted: Drafted):
    kept = drafted.drafts.save(KEY, {"notes": ["a"]}, 3, expected=0)
    kept.values["notes"].append("from the caller")
    read = drafted.drafts.read(KEY)
    assert read is not None
    read.values["notes"].append("from another caller")

    again = drafted.drafts.read(KEY)
    assert again is not None and again.values == {"notes": ["a"]}


def test_discarding_a_key_with_no_draft_is_not_an_error(drafted: Drafted):
    drafted.drafts.discard("nothing:here")

    assert drafted.drafts.read("nothing:here") is None


# ---------------------------------------------------------------------------------------------
# What is kept
# ---------------------------------------------------------------------------------------------


class Payment(StrEnum):
    CASH = "cash"
    CARD = "card"


def test_values_are_kept_as_json_carries_them(drafted: Drafted):
    draft = drafted.drafts.save(
        KEY,
        {
            "amount": Decimal("1150.50"),
            "due": date(2026, 11, 1),
            "payment": Payment.CARD,
            "lines": [{"qty": Decimal("2"), "tags": ["a", "b"]}],
        },
        3,
        expected=0,
    )

    expected_values = {
        "amount": "1150.50",
        "due": "2026-11-01",
        "payment": "card",
        "lines": [{"qty": "2", "tags": ["a", "b"]}],
    }
    assert draft.values == expected_values
    read = drafted.drafts.read(KEY)
    assert read is not None and read.values == expected_values
    assert read.kept_at == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# The key-value store's own mechanics
# ---------------------------------------------------------------------------------------------


def test_a_keep_that_won_its_slot_but_did_not_move_the_head_is_still_read():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InMemoryKeyValue(clock.now)
    drafts = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    first = drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    slot = Entry(
        key=ENTRY,
        values={"values": {"discount": "20"}, "origin_version": 3},
        version=first.version + 1,
        saved_at=clock.now(),
    )
    lagging = Draft(
        key=KEY,
        values={"discount": "20"},
        version=slot.version,
        origin_version=3,
        kept_at=slot.saved_at,
    )
    kv.add(f"{RAW}:{slot.version}", slot.model_dump_json().encode(), TTL)

    assert drafts.read(KEY) == lagging
    assert kv.get(f"{RAW}:head") == str(lagging.version).encode()
    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"discount": "30"}, 3, expected=first.version)
    assert drafts.save(KEY, {"discount": "30"}, 3, expected=lagging.version).version == (
        lagging.version + 1
    )


class StaleHead(InMemoryKeyValue):
    """A store whose first read of a head answers what it held before `meanwhile` ran: a reader
    that read the head, then lost the processor while other tabs kept."""

    meanwhile: Callable[[], None] | None = None

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        found = super().get_many(keys)
        if self.meanwhile is not None and any(key.endswith(":head") for key in keys):
            run, self.meanwhile = self.meanwhile, None
            run()
        return found


@pytest.mark.parametrize("expected", ["none", "the stale head"])
def test_a_reader_with_a_stale_head_cannot_take_a_spent_number_or_win(expected: str):
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = StaleHead(clock.now)
    drafts = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    other = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)

    def two_more_keeps() -> None:
        second = other.save(KEY, {"x": "2"}, 3, expected=first.version)
        other.save(KEY, {"x": "3"}, 3, expected=second.version)

    kv.meanwhile = two_more_keeps

    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"x": "R"}, 3, expected=0 if expected == "none" else first.version)

    current = drafts.read(KEY)
    assert current is not None and current.values == {"x": "3"}
    assert kv.get(f"{RAW}:head") == str(current.version).encode()


def test_under_racing_tabs_every_kept_draft_is_unique_and_the_last_is_what_read_gives(
    drafted: Drafted,
):
    won: list[Draft] = []
    lock = threading.Lock()
    start = threading.Barrier(6)

    def tab(number: int) -> None:
        start.wait()
        for turn in range(25):
            seen = drafted.drafts.read(KEY)
            try:
                kept = drafted.drafts.save(
                    KEY,
                    {"tab": number, "turn": turn},
                    3,
                    expected=0 if seen is None else seen.version,
                )
            except context_exceptions.DraftConflict:
                continue
            with lock:
                won.append(kept)
            if turn % 7 == 6:
                drafted.drafts.discard(KEY)

    tabs = [threading.Thread(target=tab, args=(number,)) for number in range(6)]
    for one in tabs:
        one.start()
    for one in tabs:
        one.join()

    versions = [draft.version for draft in won]
    assert won and len(set(versions)) == len(versions)
    current = drafted.drafts.read(KEY)
    if current is not None:
        assert current.version == max(versions)
        assert current in won


def test_every_key_of_a_draft_carries_the_ttl_over_redis():
    fakeredis = pytest.importorskip("fakeredis")
    from sincpro_framework.data_layer.store import RedisKeyValue

    redis = fakeredis.FakeRedis()
    drafts = Drafts(KeyValueContexts(RedisKeyValue(redis), entry_ttl=TTL), ANA)
    first = drafts.save(KEY, {"discount": "10"}, 3, expected=0)
    second = drafts.save(KEY, {"discount": "20"}, 3, expected=first.version)
    drafts.discard(KEY)

    kept = [key for key in redis.keys() if key.startswith(b"sincpro:context:entries:")]
    listed = RAW.rsplit(":", 1)[0] + ":listed"
    assert sorted(kept) == sorted(
        [
            f"{RAW}:{first.version}".encode(),
            f"{RAW}:{second.version}".encode(),
            f"{RAW}:{second.version + 1}".encode(),
            f"{RAW}:head".encode(),
            f"{RAW}:chain".encode(),
            f"{RAW}:start:0".encode(),
            listed.encode(),
            f"{listed}:1".encode(),
        ]
    )
    assert all(0 < redis.ttl(key) <= 3600 for key in kept)
    assert drafts.read(KEY) is None
    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"discount": "30"}, 3, expected=second.version)


def key_value_drafts() -> tuple[Drafts, InMemoryKeyValue, ManualClock]:
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InMemoryKeyValue(clock.now)
    return Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA), kv, clock


def test_a_head_the_store_evicted_is_found_again_from_the_chain():
    """Redis under memory pressure evicts any key: the head going is not the draft going."""
    drafts, kv, _ = key_value_drafts()
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    second = drafts.save(KEY, {"x": "2"}, 3, expected=first.version)
    kv.delete(f"{RAW}:head")

    assert drafts.read(KEY) == second
    assert kv.get(f"{RAW}:head") == str(second.version).encode()
    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"x": "stale"}, 3, expected=0)
    assert drafts.save(KEY, {"x": "3"}, 3, expected=second.version).version == (
        second.version + 1
    )


def test_over_redis_an_evicted_head_neither_hides_the_draft_nor_locks_the_key():
    fakeredis = pytest.importorskip("fakeredis")
    from sincpro_framework.data_layer.store import RedisKeyValue

    redis = fakeredis.FakeRedis()
    drafts = Drafts(KeyValueContexts(RedisKeyValue(redis), entry_ttl=TTL), ANA)
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    redis.delete(f"{RAW}:head".encode())

    assert drafts.read(KEY) == first
    assert (
        drafts.save(KEY, {"x": "2"}, 3, expected=first.version).version == first.version + 1
    )
    assert all(0 < redis.ttl(key) for key in redis.keys(b"sincpro:context:entries:*"))


def test_both_pointers_evicted_right_after_a_start_are_found_again_from_its_mark():
    drafts, kv, _ = key_value_drafts()
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    kv.delete(f"{RAW}:head")
    kv.delete(f"{RAW}:chain")

    assert drafts.read(KEY) == first
    assert (
        drafts.save(KEY, {"x": "2"}, 3, expected=first.version).version == first.version + 1
    )


class StopsOnce(InMemoryKeyValue):
    """A process that stops right before writing one key: `set` of a key ending in `stop_at`
    raises once, as a crash would leave the store."""

    stop_at: str | None = None

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        if self.stop_at is not None and key.endswith(self.stop_at):
            self.stop_at = None
            raise ConnectionError("the process stopped")
        super().set(key, value, ttl)


def test_a_process_that_stopped_after_its_slot_and_before_the_head_left_a_readable_draft():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = StopsOnce(clock.now)
    kv.stop_at = ":head"
    with pytest.raises(ConnectionError):
        Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA).save(
            KEY, {"x": "1"}, 3, expected=0
        )

    drafts = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    kept = drafts.read(KEY)
    assert kept is not None and kept.values == {"x": "1"}
    assert drafts.save(KEY, {"x": "2"}, 3, expected=kept.version).version == kept.version + 1


class StopsBeforeTheSlot(InMemoryKeyValue):
    """A process that stops after taking the start mark and before writing its first slot."""

    stopping: bool = False

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        if self.stopping and ":start:" not in key:
            self.stopping = False
            raise ConnectionError("the process stopped")
        return super().add(key, value, ttl)


def test_a_process_that_stopped_after_its_mark_and_before_its_slot_holds_the_key_briefly():
    """Inside the window the mark is a keep still writing, so the others lose; past it, the
    mark is a keep that stopped and a new chain starts over it."""
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = StopsBeforeTheSlot(clock.now)
    kv.stopping = True
    with pytest.raises(ConnectionError):
        Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA).save(
            KEY, {"x": "1"}, 3, expected=0
        )

    drafts = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    assert drafts.read(KEY) is None
    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"x": "2"}, 3, expected=0)
    clock.advance(seconds=11)
    kept = drafts.save(KEY, {"x": "2"}, 3, expected=0)
    assert drafts.read(KEY) == kept


def test_with_every_pointer_and_mark_gone_a_new_draft_never_takes_a_living_slot():
    drafts, kv, clock = key_value_drafts()
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    second = drafts.save(KEY, {"x": "2"}, 3, expected=first.version)
    for name in ("head", "chain", "start:0"):
        kv.delete(f"{RAW}:{name}")

    fresh = drafts.save(KEY, {"x": "new"}, 3, expected=0)

    assert fresh.version not in (first.version, second.version)
    assert drafts.read(KEY) == fresh
    with pytest.raises(context_exceptions.DraftConflict):
        drafts.save(KEY, {"x": "stale"}, 3, expected=second.version)


def test_memory_forgets_a_key_a_ttl_after_its_draft_and_still_never_repeats_a_version():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    store = InMemoryContexts(TTL, clock.now)
    drafts = Drafts(store, ANA)
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    drafts.discard(KEY)
    clock.advance(minutes=61)
    drafts.save("another", {"y": "1"}, 0, expected=0)

    assert ENTRY not in store._entries._versions
    fresh = drafts.save(KEY, {"x": "2"}, 3, expected=0)
    assert fresh.version > first.version


# ---------------------------------------------------------------------------------------------
# A real flow: two tabs, then the project's activation
# ---------------------------------------------------------------------------------------------


@dataclass
class Invoice(Entity):
    partner_id: str
    discount: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)


def activate(repository: MemoryRepository, drafts: Drafts, key: str, invoice_id: str) -> None:
    """The project's Command: the stored record, refused when it changed since the draft
    started, then the draft's values on it, saved — and the draft dropped."""
    draft = drafts.read(key)
    assert draft is not None
    invoice = repository.get(Invoice, invoice_id)
    assert invoice is not None
    refuse_stale(draft, invoice)
    assign(invoice, draft.values)
    repository.save(invoice)
    drafts.discard(key)


def test_two_tabs_edit_one_invoice_and_the_winner_is_saved(drafted: Drafted):
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1")
    repository.save(invoice)
    key = f"invoice:{invoice.id}:ana"

    tab_a = drafted.drafts.save(
        key, {"discount": Decimal("10"), "notes": ["a"]}, invoice.version, expected=0
    )
    with pytest.raises(context_exceptions.DraftConflict) as conflict:
        drafted.drafts.save(
            key,
            {"discount": Decimal("99"), "notes": ["b"]},
            invoice.version,
            expected=0,
        )
    seen = drafted.drafts.read(key)
    assert seen is not None and seen == tab_a
    assert conflict.value.current == tab_a.version
    drafted.drafts.save(
        key,
        {"discount": Decimal("15"), "notes": [*seen.values["notes"], "b"]},
        invoice.version,
        expected=seen.version,
    )

    activate(repository, drafted.drafts, key, invoice.id)

    stored = repository.get(Invoice, invoice.id)
    assert stored is not None
    assert (stored.discount, stored.notes) == (Decimal("15"), ["a", "b"])
    assert drafted.drafts.read(key) is None


def test_a_draft_of_a_record_someone_saved_meanwhile_is_refused_on_activation(
    drafted: Drafted,
):
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1")
    repository.save(invoice)
    key = f"invoice:{invoice.id}:ana"
    drafted.drafts.save(key, {"discount": "10", "notes": []}, invoice.version, expected=0)

    meanwhile = repository.get(Invoice, invoice.id)
    assert meanwhile is not None
    meanwhile.partner_id = "p2"
    repository.save(meanwhile)

    with pytest.raises(ddd_exceptions.StaleAggregate, match="changed since draft"):
        activate(repository, drafted.drafts, key, invoice.id)
    assert drafted.drafts.read(key) is not None
    stored = repository.get(Invoice, invoice.id)
    assert stored is not None and (stored.partner_id, stored.discount) == ("p2", Decimal("0"))


# ---------------------------------------------------------------------------------------------
# A start judged stopped: no keep is lost in silence, whatever the clocks say
# ---------------------------------------------------------------------------------------------


class InterleavesBeforeTheFirstSlot(InMemoryKeyValue):
    """A store that runs `meanwhile` once, right before the first slot of a start is written:
    a keep that took the start mark and was slow to write its slot."""

    meanwhile: Callable[[], None] | None = None

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        if self.meanwhile is not None and ":start:" not in key:
            meanwhile, self.meanwhile = self.meanwhile, None
            meanwhile()
        return super().add(key, value, ttl)


@pytest.mark.parametrize(
    ("skew", "stall"),
    [(timedelta(seconds=15), timedelta(0)), (timedelta(0), timedelta(seconds=11))],
    ids=["a replica clock 15s ahead", "a start stalled 11s"],
)
def test_a_start_judged_stopped_makes_the_slow_keep_lose_and_never_hides_the_other(
    skew: timedelta, stall: timedelta
):
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InterleavesBeforeTheFirstSlot(clock.now)
    ahead = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=lambda: clock.now() + skew), ANA)
    slow = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    answered: dict[str, Draft] = {}

    def another_tab() -> None:
        clock.advance(seconds=int(stall.total_seconds()))
        answered["other"] = ahead.save(KEY, {"who": "other"}, 3, expected=0)

    kv.meanwhile = another_tab
    with pytest.raises(context_exceptions.DraftConflict):
        slow.save(KEY, {"who": "slow"}, 3, expected=0)

    other = answered["other"]
    assert slow.read(KEY) == other
    assert ahead.read(KEY) == other
    assert ahead.save(KEY, {"who": "again"}, 3, expected=other.version).version == (
        other.version + 1
    )


def test_racing_tabs_over_a_stopped_start_leave_exactly_one_reachable_winner():
    """A mark left by a process that stopped, past the window, and eight tabs keeping at once:
    one keep wins, the rest lose, and what `read` gives is the winner."""
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = StopsBeforeTheSlot(clock.now)
    kv.stopping = True
    with pytest.raises(ConnectionError):
        Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA).save(
            KEY, {"x": "stopped"}, 3, expected=0
        )
    clock.advance(seconds=11)
    drafts = Drafts(KeyValueContexts(kv, entry_ttl=TTL, now=clock.now), ANA)
    won: list[Draft] = []
    barrier = threading.Barrier(8)

    def tab(n: int) -> None:
        barrier.wait()
        try:
            won.append(drafts.save(KEY, {"tab": n}, 3, expected=0))
        except context_exceptions.DraftConflict:
            pass

    threads = [threading.Thread(target=tab, args=(n,)) for n in range(8)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()

    assert len(won) == 1
    assert drafts.read(KEY) == won[0]


def test_a_late_pointer_write_never_moves_a_pointer_back():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InMemoryKeyValue(clock.now)
    store = KeyValueContexts(kv, entry_ttl=TTL, now=clock.now)
    drafts = Drafts(store, ANA)
    first = drafts.save(KEY, {"x": "1"}, 3, expected=0)
    second = drafts.save(KEY, {"x": "2"}, 3, expected=first.version)

    store._entries._point(ENTRY, first.version)

    for name in ("head", "chain"):
        assert kv.get(f"{RAW}:{name}") == str(second.version).encode()
    assert drafts.read(KEY) == second


def test_memory_never_repeats_a_version_under_a_frozen_clock_and_a_tiny_ttl():
    """More keeps than milliseconds of TTL: the key is forgotten while the clock has barely
    moved, and the next chain still starts above every version handed out."""
    moment = [datetime(2026, 10, 9, 12, 0, tzinfo=UTC)]
    drafts = Drafts(InMemoryContexts(timedelta(milliseconds=2), lambda: moment[0]), ANA)
    held, version = [], 0
    for n in range(5):
        version = drafts.save(KEY, {"n": n}, 0, expected=version).version
        held.append(version)
    moment[0] += timedelta(milliseconds=2)

    fresh = drafts.save(KEY, {"fresh": 1}, 0, expected=0)

    assert fresh.version not in held
    for stale in held:
        with pytest.raises(context_exceptions.DraftConflict):
            drafts.save(KEY, {"stale": 1}, 0, expected=stale)


# ---------------------------------------------------------------------------------------------
# Whose a draft is: the context says it
# ---------------------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "key_value"])
def context_store(request) -> ContextStore:
    """The context of production: the process's memory, or a key-value store such as Redis."""
    if request.param == "memory":
        return InMemoryContexts()
    return KeyValueContexts(InMemoryKeyValue())


def test_a_users_draft_is_never_read_by_another_user_for_the_same_subject(
    context_store: ContextStore,
):
    with use_context().scoped({"user_id": "ana"}) as ana:
        ana.drafts(store=context_store).save("invoice:7", {"discount": "10"}, 3, 0)

    with use_context().scoped({"user_id": "bob"}) as bob:
        assert bob.drafts(store=context_store).read("invoice:7") is None
    with use_context().scoped({"user_id": "ana"}) as ana:
        kept = ana.drafts(store=context_store).read("invoice:7")
        assert kept is not None and kept.values == {"discount": "10"}


def test_a_draft_never_crosses_tenants_even_for_the_same_user(context_store: ContextStore):
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}) as acme:
        acme.drafts(store=context_store).save("invoice:7", {"discount": "10"}, 3, 0)

    with use_context().scoped({"tenant_id": "beta", "user_id": "ana"}) as beta:
        assert beta.drafts(store=context_store).read("invoice:7") is None


def test_a_draft_may_belong_to_a_session_or_to_any_keys_the_project_chooses(
    context_store: ContextStore,
):
    with use_context().scoped({"session_id": "s-1"}) as first:
        first.drafts(by=["session_id"], store=context_store).save("cart", {"items": 2}, 0, 0)

    with use_context().scoped({"session_id": "s-2"}) as second:
        assert second.drafts(by=["session_id"], store=context_store).read("cart") is None
    with use_context().scoped({"user_id": "ana", "pos_id": 4}) as pos:
        drafts = pos.drafts(by=["user_id", "pos_id"], store=context_store)
        assert drafts.owner == "user_id=ana,pos_id=4"


def test_a_draft_with_no_user_in_the_context_is_kept_for_the_tenant_and_refused_nowhere(
    context_store: ContextStore,
):
    """A system with no users is still a system: nothing is refused, and what is kept is the
    tenant's, shared by whoever the context does not tell apart."""
    with use_context().scoped({"tenant_id": "acme"}) as nobody:
        drafts = nobody.drafts(store=context_store)
        drafts.save("invoice:7", {"discount": "10"}, 3, 0)

    assert drafts.owner == "tenant_id=acme,tenant_ids=,user_id="
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}) as ana:
        assert ana.drafts(store=context_store).read("invoice:7") is None
    with use_context().scoped({"tenant_id": "globex"}) as other:
        assert other.drafts(store=context_store).read("invoice:7") is None


def test_the_drafts_are_kept_where_the_context_is_kept():
    """The nearest `ContextStore` of the context — what `bus.store_context_in(store)` sets — keeps
    the drafts too, on the same storage."""
    kv = InMemoryKeyValue()
    store = KeyValueContexts(kv, prefix="billing")
    with use_context().scoped({ContextStore: store, "user_id": "ana"}) as scope:
        scope.drafts().save("invoice:7", {"discount": "10"}, 3, 0)

    assert any(
        key.startswith(
            "billing:entries:SINCPRO_ENTITY_DRAFT_CONTEXT:tenant_id=,tenant_ids=,user_id=ana:"
            "invoice%3A7"
        )
        for key in kv._values
    )


def test_a_context_store_of_a_project_that_saves_no_entries_says_so():
    class OnlyContexts(ContextStore):
        def save(self, key, values, ttl=None) -> None: ...

        def load(self, key):
            return None

        def delete(self, key) -> None: ...

        def version(self, key) -> int:
            return 0

    with use_context().scoped({"user_id": "ana"}) as scope:
        with pytest.raises(exceptions.ProgrammingError, match="saves no entries"):
            scope.drafts(store=OnlyContexts()).save("invoice:7", {}, 0, 0)


@pytest.mark.parametrize(
    "make", [InMemoryContexts, lambda: KeyValueContexts(InMemoryKeyValue())]
)
def test_a_context_kept_by_name_and_a_draft_never_share_a_key(make):
    """A project may keep a context under any name it likes — even the draft's own: what the
    framework keeps beside the contexts lives apart from them."""
    store = make()
    drafts = Drafts(store, ANA)
    drafts.save(KEY, {"discount": "10"}, 3, 0)

    store.save(ENTRY, {"lang": "es"})

    read = drafts.read(KEY)
    assert read is not None and read.values == {"discount": "10"}
    assert store.load(ENTRY) == {"lang": "es"}


def test_a_feature_keeps_the_callers_draft_where_its_bus_keeps_the_context():
    """`bus.store_context_in(store)` is all a project configures: a Redis in production, the process
    in tests — the drafts of every caller are kept there, each under whose it is."""
    billing = UseFramework("drafting-billing", log_after_execution=False)
    billing.store_context_in(KeyValueContexts(InMemoryKeyValue()))

    class CommandSaveDraft(DataTransferObject):
        invoice: str
        discount: str

    @billing.feature(CommandSaveDraft)
    class SaveDraft(Feature):
        def execute(self, dto: CommandSaveDraft) -> str:
            """Answers the discount the caller's draft held before this keep."""
            drafts = self.context.drafts()
            read = drafts.read(dto.invoice)
            drafts.save(
                dto.invoice, {"discount": dto.discount}, 1, read.version if read else 0
            )
            return read.values["discount"] if read else ""

    with billing.context({"user_id": "ana"}):
        first = billing(CommandSaveDraft(invoice="invoice:7", discount="10"))
        second = billing(CommandSaveDraft(invoice="invoice:7", discount="20"))
    with billing.context({"user_id": "bob"}):
        bobs = billing(CommandSaveDraft(invoice="invoice:7", discount="99"))

    assert (first, second, bobs) == ("", "10", "")
