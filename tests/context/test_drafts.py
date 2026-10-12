"""DraftStore kept between requests: optimistic, with a TTL, the same on both stores.

Every case runs against `InMemoryDrafts` and against `KeyValueDrafts` over a `KeyValueStore`
(in memory here, Redis when `fakeredis` is installed), judged by one clock.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest

from sincpro_framework import (
    DataTransferObject,
    FailureKind,
    Feature,
    ProgrammingError,
    UseFramework,
)
from sincpro_framework.common.failures import refined_failure_kind
from sincpro_framework.common.store.in_memory import InMemoryKeyValue
from sincpro_framework.context import (
    ContextStore,
    Draft,
    DraftConflict,
    DraftStore,
    InMemoryContexts,
    InMemoryDrafts,
    KeyValueContexts,
    KeyValueDrafts,
    use_context,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import assign, refuse_stale
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.exceptions import StaleAggregate
from sincpro_framework.entrypoints.adapters.cron.adapters.clocks import ManualClock
from sincpro_framework.exceptions import ContextRequired

TTL = timedelta(hours=1)
OWNER = "user_id=ana"
KEY = "invoice:7"


@dataclass
class Drafted:
    drafts: DraftStore
    clock: ManualClock
    kv: InMemoryKeyValue | None = None


@pytest.fixture(params=["memory", "key_value"])
def drafted(request) -> Iterator[Drafted]:
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    if request.param == "memory":
        yield Drafted(InMemoryDrafts(TTL, clock.now), clock)
    else:
        kv = InMemoryKeyValue(clock.now)
        yield Drafted(KeyValueDrafts(kv, TTL, now=clock.now), clock, kv)


# ---------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------


def test_the_first_keep_names_version_zero_and_answers_the_draft_read_gives(drafted: Drafted):
    draft = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, origin_version=3, expected=0)

    assert draft.version > 0
    assert (draft.origin_version, draft.values) == (3, {"discount": "10"})
    assert drafted.drafts.read(OWNER, KEY) == draft


def test_a_keep_naming_a_version_that_is_gone_is_a_conflict_carrying_the_current(
    drafted: Drafted,
):
    first = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    second = drafted.drafts.keep(OWNER, KEY, {"discount": "20"}, 3, expected=first.version)

    with pytest.raises(DraftConflict) as raised:
        drafted.drafts.keep(OWNER, KEY, {"discount": "30"}, 3, expected=first.version)

    assert second.version > first.version
    assert raised.value.current == second.version
    assert refined_failure_kind(raised.value) is FailureKind.CONFLICT
    current = drafted.drafts.read(OWNER, KEY)
    assert current is not None and current.values == {"discount": "20"}


def test_starting_a_draft_over_a_live_one_is_a_conflict(drafted: Drafted):
    first = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)

    with pytest.raises(DraftConflict) as raised:
        drafted.drafts.keep(OWNER, KEY, {"discount": "99"}, 3, expected=0)

    assert raised.value.current == first.version


def test_of_many_keeps_naming_the_same_version_exactly_one_wins(drafted: Drafted):
    first = drafted.drafts.keep(OWNER, KEY, {"n": 0}, 3, expected=0)
    won: list[int] = []
    lost: list[int] = []
    start = threading.Barrier(8)

    def tab(number: int) -> None:
        start.wait()
        try:
            drafted.drafts.keep(OWNER, KEY, {"n": number}, 3, expected=first.version)
            won.append(number)
        except DraftConflict:
            lost.append(number)

    tabs = [threading.Thread(target=tab, args=(number,)) for number in range(8)]
    for one in tabs:
        one.start()
    for one in tabs:
        one.join()

    assert len(won) == 1 and len(lost) == 7
    current = drafted.drafts.read(OWNER, KEY)
    assert current is not None and current.version > first.version
    assert current.values == {"n": won[0]}


# ---------------------------------------------------------------------------------------------
# TTL and discard
# ---------------------------------------------------------------------------------------------


def test_an_abandoned_draft_disappears_after_its_ttl(drafted: Drafted):
    drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)

    drafted.clock.advance(minutes=61)

    assert drafted.drafts.read(OWNER, KEY) is None


def test_each_keep_starts_the_ttl_again(drafted: Drafted):
    first = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    drafted.clock.advance(minutes=50)
    drafted.drafts.keep(OWNER, KEY, {"discount": "20"}, 3, expected=first.version)
    drafted.clock.advance(minutes=50)

    current = drafted.drafts.read(OWNER, KEY)
    assert current is not None and current.values == {"discount": "20"}


def test_after_an_expiry_the_next_draft_starts_from_zero_and_never_repeats_a_version(
    drafted: Drafted,
):
    first = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    drafted.clock.advance(minutes=30)
    second = drafted.drafts.keep(OWNER, KEY, {"discount": "20"}, 3, expected=first.version)
    drafted.clock.advance(minutes=61)

    with pytest.raises(DraftConflict):
        drafted.drafts.keep(OWNER, KEY, {"discount": "30"}, 3, expected=second.version)
    fresh = drafted.drafts.keep(OWNER, KEY, {"discount": "30"}, 3, expected=0)

    assert fresh.version not in (first.version, second.version)
    assert drafted.drafts.read(OWNER, KEY) == fresh


def test_a_tab_holding_a_draft_from_before_an_expiry_never_overwrites_the_new_one(
    drafted: Drafted,
):
    """Tab A keeps, goes quiet past the TTL; tab B starts a new draft. A's version is from the
    expired chain and must lose — a version handed out again would let A win in silence."""
    tab_a = drafted.drafts.keep(OWNER, KEY, {"x": "A"}, 3, expected=0)
    drafted.clock.advance(minutes=61)
    tab_b = drafted.drafts.keep(OWNER, KEY, {"x": "B"}, 3, expected=0)

    with pytest.raises(DraftConflict) as raised:
        drafted.drafts.keep(OWNER, KEY, {"x": "A-stale"}, 3, expected=tab_a.version)

    assert tab_b.version != tab_a.version
    assert raised.value.current == tab_b.version
    current = drafted.drafts.read(OWNER, KEY)
    assert current is not None and current.values == {"x": "B"}


def test_a_discarded_draft_is_gone_and_a_tab_holding_it_cannot_bring_it_back(
    drafted: Drafted,
):
    first = drafted.drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    second = drafted.drafts.keep(OWNER, KEY, {"discount": "20"}, 3, expected=first.version)

    drafted.drafts.discard(OWNER, KEY)

    assert drafted.drafts.read(OWNER, KEY) is None
    with pytest.raises(DraftConflict):
        drafted.drafts.keep(OWNER, KEY, {"discount": "stale"}, 3, expected=second.version)
    again = drafted.drafts.keep(OWNER, KEY, {"discount": "40"}, 3, expected=0)
    assert again.version > second.version
    assert drafted.drafts.read(OWNER, KEY) == again


def test_what_a_caller_holds_is_a_copy_of_what_is_kept(drafted: Drafted):
    kept = drafted.drafts.keep(OWNER, KEY, {"notes": ["a"]}, 3, expected=0)
    kept.values["notes"].append("from the caller")
    read = drafted.drafts.read(OWNER, KEY)
    assert read is not None
    read.values["notes"].append("from another caller")

    again = drafted.drafts.read(OWNER, KEY)
    assert again is not None and again.values == {"notes": ["a"]}


def test_discarding_a_key_with_no_draft_is_not_an_error(drafted: Drafted):
    drafted.drafts.discard(OWNER, "nothing:here")

    assert drafted.drafts.read(OWNER, "nothing:here") is None


# ---------------------------------------------------------------------------------------------
# What is kept
# ---------------------------------------------------------------------------------------------


class Payment(StrEnum):
    CASH = "cash"
    CARD = "card"


def test_values_are_kept_as_json_carries_them(drafted: Drafted):
    draft = drafted.drafts.keep(
        OWNER,
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
    read = drafted.drafts.read(OWNER, KEY)
    assert read is not None and read.values == expected_values
    assert read.kept_at == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# The key-value store's own mechanics
# ---------------------------------------------------------------------------------------------


def test_a_keep_that_won_its_slot_but_did_not_move_the_head_is_still_read():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InMemoryKeyValue(clock.now)
    drafts = KeyValueDrafts(kv, TTL, now=clock.now)
    first = drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    lagging = Draft(
        key=f"{OWNER}/{KEY}",
        values={"discount": "20"},
        version=first.version + 1,
        origin_version=3,
        kept_at=clock.now(),
    )
    kv.add(
        f"sincpro:draft:{OWNER}/{KEY}:{lagging.version}",
        lagging.model_dump_json().encode(),
        TTL,
    )

    assert drafts.read(OWNER, KEY) == lagging
    assert kv.get(f"sincpro:draft:{OWNER}/{KEY}:head") == str(lagging.version).encode()
    with pytest.raises(DraftConflict):
        drafts.keep(OWNER, KEY, {"discount": "30"}, 3, expected=first.version)
    assert drafts.keep(
        OWNER, KEY, {"discount": "30"}, 3, expected=lagging.version
    ).version == (lagging.version + 1)


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
    drafts = KeyValueDrafts(kv, TTL, now=clock.now)
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    other = KeyValueDrafts(kv, TTL, now=clock.now)

    def two_more_keeps() -> None:
        second = other.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version)
        other.keep(OWNER, KEY, {"x": "3"}, 3, expected=second.version)

    kv.meanwhile = two_more_keeps

    with pytest.raises(DraftConflict):
        drafts.keep(
            OWNER, KEY, {"x": "R"}, 3, expected=0 if expected == "none" else first.version
        )

    current = drafts.read(OWNER, KEY)
    assert current is not None and current.values == {"x": "3"}
    assert kv.get(f"sincpro:draft:{OWNER}/{KEY}:head") == str(current.version).encode()


def test_under_racing_tabs_every_kept_draft_is_unique_and_the_last_is_what_read_gives(
    drafted: Drafted,
):
    won: list[Draft] = []
    lock = threading.Lock()
    start = threading.Barrier(6)

    def tab(number: int) -> None:
        start.wait()
        for turn in range(25):
            seen = drafted.drafts.read(OWNER, KEY)
            try:
                kept = drafted.drafts.keep(
                    OWNER,
                    KEY,
                    {"tab": number, "turn": turn},
                    3,
                    expected=0 if seen is None else seen.version,
                )
            except DraftConflict:
                continue
            with lock:
                won.append(kept)
            if turn % 7 == 6:
                drafted.drafts.discard(OWNER, KEY)

    tabs = [threading.Thread(target=tab, args=(number,)) for number in range(6)]
    for one in tabs:
        one.start()
    for one in tabs:
        one.join()

    versions = [draft.version for draft in won]
    assert won and len(set(versions)) == len(versions)
    current = drafted.drafts.read(OWNER, KEY)
    if current is not None:
        assert current.version == max(versions)
        assert current in won


def test_every_key_of_a_draft_carries_the_ttl_over_redis():
    fakeredis = pytest.importorskip("fakeredis")
    from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue

    redis = fakeredis.FakeRedis()
    drafts = KeyValueDrafts(RedisKeyValue(redis), TTL)
    first = drafts.keep(OWNER, KEY, {"discount": "10"}, 3, expected=0)
    second = drafts.keep(OWNER, KEY, {"discount": "20"}, 3, expected=first.version)
    drafts.discard(OWNER, KEY)

    kept = [key for key in redis.keys() if key.startswith(b"sincpro:draft:")]
    assert sorted(kept) == sorted(
        [
            f"sincpro:draft:{OWNER}/{KEY}:{first.version}".encode(),
            f"sincpro:draft:{OWNER}/{KEY}:{second.version}".encode(),
            f"sincpro:draft:{OWNER}/{KEY}:{second.version + 1}".encode(),
            f"sincpro:draft:{OWNER}/{KEY}:head".encode(),
            f"sincpro:draft:{OWNER}/{KEY}:chain".encode(),
            f"sincpro:draft:{OWNER}/{KEY}:start:0".encode(),
            f"sincpro:draft:{OWNER}:subjects".encode(),
        ]
    )
    assert all(0 < redis.ttl(key) <= 3600 for key in kept)
    assert drafts.read(OWNER, KEY) is None
    with pytest.raises(DraftConflict):
        drafts.keep(OWNER, KEY, {"discount": "30"}, 3, expected=second.version)


def key_value_drafts() -> tuple[KeyValueDrafts, InMemoryKeyValue, ManualClock]:
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = InMemoryKeyValue(clock.now)
    return KeyValueDrafts(kv, TTL, now=clock.now), kv, clock


def test_a_head_the_store_evicted_is_found_again_from_the_chain():
    """Redis under memory pressure evicts any key: the head going is not the draft going."""
    drafts, kv, _ = key_value_drafts()
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    second = drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version)
    kv.delete(f"sincpro:draft:{OWNER}/{KEY}:head")

    assert drafts.read(OWNER, KEY) == second
    assert kv.get(f"sincpro:draft:{OWNER}/{KEY}:head") == str(second.version).encode()
    with pytest.raises(DraftConflict):
        drafts.keep(OWNER, KEY, {"x": "stale"}, 3, expected=0)
    assert drafts.keep(OWNER, KEY, {"x": "3"}, 3, expected=second.version).version == (
        second.version + 1
    )


def test_over_redis_an_evicted_head_neither_hides_the_draft_nor_locks_the_key():
    fakeredis = pytest.importorskip("fakeredis")
    from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue

    redis = fakeredis.FakeRedis()
    drafts = KeyValueDrafts(RedisKeyValue(redis), TTL)
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    redis.delete(f"sincpro:draft:{OWNER}/{KEY}:head".encode())

    assert drafts.read(OWNER, KEY) == first
    assert (
        drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version).version
        == first.version + 1
    )
    assert all(0 < redis.ttl(key) for key in redis.keys(b"sincpro:draft:*"))


def test_both_pointers_evicted_right_after_a_start_are_found_again_from_its_mark():
    drafts, kv, _ = key_value_drafts()
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    kv.delete(f"sincpro:draft:{OWNER}/{KEY}:head")
    kv.delete(f"sincpro:draft:{OWNER}/{KEY}:chain")

    assert drafts.read(OWNER, KEY) == first
    assert (
        drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version).version
        == first.version + 1
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
        KeyValueDrafts(kv, TTL, now=clock.now).keep(OWNER, KEY, {"x": "1"}, 3, expected=0)

    drafts = KeyValueDrafts(kv, TTL, now=clock.now)
    kept = drafts.read(OWNER, KEY)
    assert kept is not None and kept.values == {"x": "1"}
    assert (
        drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=kept.version).version
        == kept.version + 1
    )


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
        KeyValueDrafts(kv, TTL, now=clock.now).keep(OWNER, KEY, {"x": "1"}, 3, expected=0)

    drafts = KeyValueDrafts(kv, TTL, now=clock.now)
    assert drafts.read(OWNER, KEY) is None
    with pytest.raises(DraftConflict):
        drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=0)
    clock.advance(seconds=11)
    kept = drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=0)
    assert drafts.read(OWNER, KEY) == kept


def test_with_every_pointer_and_mark_gone_a_new_draft_never_takes_a_living_slot():
    drafts, kv, clock = key_value_drafts()
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    second = drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version)
    for name in ("head", "chain", "start:0"):
        kv.delete(f"sincpro:draft:{OWNER}/{KEY}:{name}")

    fresh = drafts.keep(OWNER, KEY, {"x": "new"}, 3, expected=0)

    assert fresh.version not in (first.version, second.version)
    assert drafts.read(OWNER, KEY) == fresh
    with pytest.raises(DraftConflict):
        drafts.keep(OWNER, KEY, {"x": "stale"}, 3, expected=second.version)


def test_memory_forgets_a_key_a_ttl_after_its_draft_and_still_never_repeats_a_version():
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    drafts = InMemoryDrafts(TTL, clock.now)
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    drafts.discard(OWNER, KEY)
    clock.advance(minutes=61)
    drafts.keep(OWNER, "another", {"y": "1"}, 0, expected=0)

    assert KEY not in drafts._versions
    fresh = drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=0)
    assert fresh.version > first.version


# ---------------------------------------------------------------------------------------------
# A real flow: two tabs, then the project's activation
# ---------------------------------------------------------------------------------------------


@dataclass
class Invoice(Entity):
    partner_id: str
    discount: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)


def activate(
    repository: MemoryRepository, drafts: DraftStore, key: str, invoice_id: str
) -> None:
    """The project's Command: the stored record, refused when it changed since the draft
    started, then the draft's values on it, saved — and the draft dropped."""
    draft = drafts.read(OWNER, key)
    assert draft is not None
    invoice = repository.get(Invoice, invoice_id)
    assert invoice is not None
    refuse_stale(draft, invoice)
    assign(invoice, draft.values)
    repository.save(invoice)
    drafts.discard(OWNER, key)


def test_two_tabs_edit_one_invoice_and_the_winner_is_saved(drafted: Drafted):
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1")
    repository.save(invoice)
    key = f"invoice:{invoice.id}:ana"

    tab_a = drafted.drafts.keep(
        OWNER, key, {"discount": Decimal("10"), "notes": ["a"]}, invoice.version, expected=0
    )
    with pytest.raises(DraftConflict) as conflict:
        drafted.drafts.keep(
            OWNER,
            key,
            {"discount": Decimal("99"), "notes": ["b"]},
            invoice.version,
            expected=0,
        )
    seen = drafted.drafts.read(OWNER, key)
    assert seen is not None and seen == tab_a
    assert conflict.value.current == tab_a.version
    drafted.drafts.keep(
        OWNER,
        key,
        {"discount": Decimal("15"), "notes": [*seen.values["notes"], "b"]},
        invoice.version,
        expected=seen.version,
    )

    activate(repository, drafted.drafts, key, invoice.id)

    stored = repository.get(Invoice, invoice.id)
    assert stored is not None
    assert (stored.discount, stored.notes) == (Decimal("15"), ["a", "b"])
    assert drafted.drafts.read(OWNER, key) is None


def test_a_draft_of_a_record_someone_saved_meanwhile_is_refused_on_activation(
    drafted: Drafted,
):
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1")
    repository.save(invoice)
    key = f"invoice:{invoice.id}:ana"
    drafted.drafts.keep(
        OWNER, key, {"discount": "10", "notes": []}, invoice.version, expected=0
    )

    meanwhile = repository.get(Invoice, invoice.id)
    assert meanwhile is not None
    meanwhile.partner_id = "p2"
    repository.save(meanwhile)

    with pytest.raises(StaleAggregate, match="changed since draft"):
        activate(repository, drafted.drafts, key, invoice.id)
    assert drafted.drafts.read(OWNER, key) is not None
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
    ahead = KeyValueDrafts(kv, TTL, now=lambda: clock.now() + skew)
    slow = KeyValueDrafts(kv, TTL, now=clock.now)
    answered: dict[str, Draft] = {}

    def another_tab() -> None:
        clock.advance(seconds=int(stall.total_seconds()))
        answered["other"] = ahead.keep(OWNER, KEY, {"who": "other"}, 3, expected=0)

    kv.meanwhile = another_tab
    with pytest.raises(DraftConflict):
        slow.keep(OWNER, KEY, {"who": "slow"}, 3, expected=0)

    other = answered["other"]
    assert slow.read(OWNER, KEY) == other
    assert ahead.read(OWNER, KEY) == other
    assert ahead.keep(OWNER, KEY, {"who": "again"}, 3, expected=other.version).version == (
        other.version + 1
    )


def test_racing_tabs_over_a_stopped_start_leave_exactly_one_reachable_winner():
    """A mark left by a process that stopped, past the window, and eight tabs keeping at once:
    one keep wins, the rest lose, and what `read` gives is the winner."""
    clock = ManualClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    kv = StopsBeforeTheSlot(clock.now)
    kv.stopping = True
    with pytest.raises(ConnectionError):
        KeyValueDrafts(kv, TTL, now=clock.now).keep(
            OWNER, KEY, {"x": "stopped"}, 3, expected=0
        )
    clock.advance(seconds=11)
    drafts = KeyValueDrafts(kv, TTL, now=clock.now)
    won: list[Draft] = []
    barrier = threading.Barrier(8)

    def tab(n: int) -> None:
        barrier.wait()
        try:
            won.append(drafts.keep(OWNER, KEY, {"tab": n}, 3, expected=0))
        except DraftConflict:
            pass

    threads = [threading.Thread(target=tab, args=(n,)) for n in range(8)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()

    assert len(won) == 1
    assert drafts.read(OWNER, KEY) == won[0]


def test_a_late_pointer_write_never_moves_a_pointer_back():
    drafts, kv, _ = key_value_drafts()
    first = drafts.keep(OWNER, KEY, {"x": "1"}, 3, expected=0)
    second = drafts.keep(OWNER, KEY, {"x": "2"}, 3, expected=first.version)

    drafts._point(KEY, first.version)

    for name in ("head", "chain"):
        assert kv.get(f"sincpro:draft:{OWNER}/{KEY}:{name}") == str(second.version).encode()
    assert drafts.read(OWNER, KEY) == second


def test_memory_never_repeats_a_version_under_a_frozen_clock_and_a_tiny_ttl():
    """More keeps than milliseconds of TTL: the key is forgotten while the clock has barely
    moved, and the next chain still starts above every version handed out."""
    moment = [datetime(2026, 10, 9, 12, 0, tzinfo=UTC)]
    drafts = InMemoryDrafts(timedelta(milliseconds=2), lambda: moment[0])
    held, version = [], 0
    for n in range(5):
        version = drafts.keep(OWNER, KEY, {"n": n}, 0, expected=version).version
        held.append(version)
    moment[0] += timedelta(milliseconds=2)

    fresh = drafts.keep(OWNER, KEY, {"fresh": 1}, 0, expected=0)

    assert fresh.version not in held
    for stale in held:
        with pytest.raises(DraftConflict):
            drafts.keep(OWNER, KEY, {"stale": 1}, 0, expected=stale)


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
        ana.drafts(store=context_store).keep("invoice:7", {"discount": "10"}, 3, 0)

    with use_context().scoped({"user_id": "bob"}) as bob:
        assert bob.drafts(store=context_store).read("invoice:7") is None
    with use_context().scoped({"user_id": "ana"}) as ana:
        kept = ana.drafts(store=context_store).read("invoice:7")
        assert kept is not None and kept.values == {"discount": "10"}


def test_a_draft_never_crosses_tenants_even_for_the_same_user(context_store: ContextStore):
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}) as acme:
        acme.drafts(store=context_store).keep("invoice:7", {"discount": "10"}, 3, 0)

    with use_context().scoped({"tenant_id": "beta", "user_id": "ana"}) as beta:
        assert beta.drafts(store=context_store).read("invoice:7") is None


def test_a_draft_may_belong_to_a_session_or_to_any_keys_the_project_chooses(
    context_store: ContextStore,
):
    with use_context().scoped({"session_id": "s-1"}) as first:
        first.drafts(by=["session_id"], store=context_store).keep("cart", {"items": 2}, 0, 0)

    with use_context().scoped({"session_id": "s-2"}) as second:
        assert second.drafts(by=["session_id"], store=context_store).read("cart") is None
    with use_context().scoped({"user_id": "ana", "pos_id": 4}) as pos:
        drafts = pos.drafts(by=["user_id", "pos_id"], store=context_store)
        assert drafts.owner == "user_id=ana/pos_id=4"


def test_a_draft_with_no_owner_in_the_context_is_refused_naming_what_is_missing(
    context_store: ContextStore,
):
    with use_context().scoped({"tenant_id": "acme"}) as nobody:
        with pytest.raises(ContextRequired) as refused:
            nobody.drafts(store=context_store)

    assert refused.value.missing == ["user_id"]


def test_the_drafts_are_kept_where_the_context_is_kept():
    """The nearest `ContextStore` of the context — what `bus.context_store(store)` sets — keeps
    the drafts too, on the same storage."""
    kv = InMemoryKeyValue()
    store = KeyValueContexts(kv, prefix="billing")
    with use_context().scoped({ContextStore: store, "user_id": "ana"}) as scope:
        scope.drafts().keep("invoice:7", {"discount": "10"}, 3, 0)

    assert any(key.startswith("billing:draft:user_id=ana/invoice:7") for key in kv._values)


def test_a_context_store_that_keeps_no_drafts_says_so():
    class OnlyContexts(InMemoryContexts):
        def drafts(self):
            return ContextStore.drafts(self)

    with use_context().scoped({"user_id": "ana"}) as scope:
        with pytest.raises(ProgrammingError, match="keeps no drafts"):
            scope.drafts(store=OnlyContexts())


def test_every_draft_store_is_a_key_value_or_memory_one():
    assert isinstance(InMemoryContexts().drafts(), InMemoryDrafts)
    assert isinstance(KeyValueContexts(InMemoryKeyValue()).drafts(), KeyValueDrafts)


def test_a_feature_keeps_the_callers_draft_where_its_bus_keeps_the_context():
    """`bus.context_store(store)` is all a project configures: a Redis in production, the process
    in tests — the drafts of every caller are kept there, each under whose it is."""
    billing = UseFramework("drafting-billing", log_after_execution=False)
    billing.context_store(KeyValueContexts(InMemoryKeyValue()))

    class CommandSaveDraft(DataTransferObject):
        invoice: str
        discount: str

    @billing.feature(CommandSaveDraft)
    class SaveDraft(Feature):
        def execute(self, dto: CommandSaveDraft) -> str:
            """Answers the discount the caller's draft held before this keep."""
            drafts = self.context.drafts()
            read = drafts.read(dto.invoice)
            drafts.keep(
                dto.invoice, {"discount": dto.discount}, 1, read.version if read else 0
            )
            return read.values["discount"] if read else ""

    with billing.context({"user_id": "ana"}):
        first = billing(CommandSaveDraft(invoice="invoice:7", discount="10"))
        second = billing(CommandSaveDraft(invoice="invoice:7", discount="20"))
    with billing.context({"user_id": "bob"}):
        bobs = billing(CommandSaveDraft(invoice="invoice:7", discount="99"))

    assert (first, second, bobs) == ("", "10", "")
