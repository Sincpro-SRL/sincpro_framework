"""The contract suites a caching strategy of a project's passes — the same the built-ins pass.

    class TestMyCodec(CodecContract):
        def make_codec(self): return MsgpackCodec(Tenant)
        def samples(self): return [Tenant(name="acme", version="v1")]

    class TestMyEviction(EvictionContract):
        bound = 500
        def make_eviction(self): return TinyLfu(max_entries=500)

    class TestMyFreshness(FreshnessContract):
        def make_freshness(self): return BusinessHours(ttl=timedelta(minutes=5))

Context: a `Cache` trusts its strategies blindly — a codec that does not round-trip serves a
different value than it kept, an eviction that does not hold its bound leaks memory, a freshness
that answers a `stale_until` before `fresh_until` serves nothing it computed. Each suite is the
contract of its port (PRD_13 §7, §8) written as tests: inherit one, answer its factory, and run it
with pytest. Pytest-free, like `KeyValueStoreContract`: a failure is an `AssertionError`.
"""

import threading
from typing import Any

from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.eviction import Eviction
from sincpro_framework.caching.domain.freshness import NEVER, Freshness

MOMENTS = (0.0, 1_000.0, 1_790_000_000.5)
ROLLS = (0.0, 0.25, 0.5, 0.999999)


class CodecContract:
    """Inherit, answer `make_codec` and `samples`, and run with pytest."""

    def make_codec(self) -> Codec[Any]:
        raise NotImplementedError("answer the codec under test")

    def samples(self) -> list[Any]:
        raise NotImplementedError("answer values of the shape the codec encodes")

    def foreign_bytes(self) -> list[bytes]:
        """Bytes that are not this shape — what another deploy, or a torn write, left."""
        return [b"", b"\xff\xfe\x00 not this shape"]

    def make_other_codec(self) -> Codec[Any] | None:
        """A codec of another shape, whose `schema` must differ — `None` skips that test."""
        return None

    def test_what_it_encodes_it_decodes_back(self) -> None:
        codec = self.make_codec()
        for value in self.samples():
            if codec.decode(codec.encode(value)) != value:
                raise AssertionError(f"{value!r} did not round-trip")

    def test_it_encodes_to_bytes(self) -> None:
        codec = self.make_codec()
        for value in self.samples():
            if not isinstance(codec.encode(value), bytes):
                raise AssertionError("encode did not answer bytes")

    def test_bytes_of_another_shape_raise(self) -> None:
        """The cache counts a raise as a miss; a value decoded half-right reaches the caller."""
        codec = self.make_codec()
        for raw in self.foreign_bytes():
            try:
                decoded = codec.decode(raw)
            except Exception:
                continue
            raise AssertionError(f"{raw!r} decoded to {decoded!r} instead of raising")

    def test_its_schema_is_the_same_for_the_same_shape(self) -> None:
        """The schema is part of the key: one that changed per instance would miss every time."""
        first, second = self.make_codec().schema, self.make_codec().schema
        if not first or first != second:
            raise AssertionError(
                "schema is empty, or differs between two codecs of one shape"
            )

    def test_another_shape_has_another_schema(self) -> None:
        other = self.make_other_codec()
        if other is None:
            return
        if other.schema == self.make_codec().schema:
            raise AssertionError("two shapes share a schema: a deploy would read the old one")


class FreshnessContract:
    """Inherit, answer `make_freshness`, and run with pytest."""

    def make_freshness(self) -> Freshness:
        raise NotImplementedError("answer the freshness under test")

    def test_a_value_is_fresh_from_the_moment_it_is_kept(self) -> None:
        freshness = self.make_freshness()
        for now in MOMENTS:
            for roll in ROLLS:
                if freshness.fresh_until(now, roll) < now:
                    raise AssertionError(f"fresh_until({now}, {roll}) is before now")

    def test_stale_until_is_never_before_fresh_until(self) -> None:
        freshness = self.make_freshness()
        for now in MOMENTS:
            for roll in ROLLS:
                fresh_until = freshness.fresh_until(now, roll)
                if freshness.stale_until(fresh_until) < fresh_until:
                    raise AssertionError("stale_until is before fresh_until")

    def test_the_same_arguments_answer_the_same(self) -> None:
        """Pure: the cache replays a moment, and a test fixes the roll."""
        one, other = self.make_freshness(), self.make_freshness()
        for now in MOMENTS:
            for roll in ROLLS:
                fresh_until = one.fresh_until(now, roll)
                if fresh_until != other.fresh_until(
                    now, roll
                ) or fresh_until != one.fresh_until(now, roll):
                    raise AssertionError("fresh_until answered differently for one moment")
                if one.stale_until(fresh_until) != other.stale_until(fresh_until):
                    raise AssertionError("stale_until answered differently for one moment")
                if one.on_hit(now, fresh_until, now, roll) != other.on_hit(
                    now, fresh_until, now, roll
                ):
                    raise AssertionError("on_hit answered differently for one moment")

    def test_a_hit_never_shortens_a_value_or_renews_it_into_the_past(self) -> None:
        freshness = self.make_freshness()
        for born in MOMENTS:
            fresh_until = freshness.fresh_until(born, 0.5)
            for elapsed in (0.0, 1.0, 60.0):
                now = born + elapsed
                renewed = freshness.on_hit(born, fresh_until, now, 0.5)
                if renewed is not None and (renewed < fresh_until or renewed < now):
                    raise AssertionError(f"on_hit at {now} answered {renewed}")

    def test_a_value_that_never_expires_is_never_recomputed_early(self) -> None:
        freshness = self.make_freshness()
        for roll in ROLLS:
            if freshness.recompute_early(NEVER, 10.0, 1_000.0, roll):
                raise AssertionError("a value that never expires was picked to recompute")

    def test_recompute_early_answers_a_bool(self) -> None:
        freshness = self.make_freshness()
        fresh_until = freshness.fresh_until(0.0, 0.5)
        early = freshness.recompute_early(fresh_until, 0.1, 0.0, 0.5)
        if not isinstance(early, bool):
            raise AssertionError("recompute_early did not answer a bool")


class EvictionContract:
    """Inherit, set `bound` (`None` for an unbounded one), answer `make_eviction`, and run."""

    bound: int | None = None
    racers = 8

    def make_eviction(self) -> Eviction:
        raise NotImplementedError("answer a new eviction, bounded by `bound`")

    def _admitted(self, eviction: Eviction, kept: set[str], key: str) -> list[str]:
        """What the process tier does: keep `key`, drop what the eviction names."""
        kept.add(key)
        victims = eviction.admitted(key)
        kept.difference_update(victims)
        return victims

    def test_the_tier_holds_no_more_than_its_bound(self) -> None:
        eviction, kept = self.make_eviction(), set[str]()
        for n in range(3 * (self.bound or 50)):
            self._admitted(eviction, kept, f"k{n}")
            if self.bound is not None and len(kept) > self.bound:
                raise AssertionError(f"{len(kept)} kept past a bound of {self.bound}")

    def test_the_key_just_admitted_is_never_its_victim(self) -> None:
        eviction, kept = self.make_eviction(), set[str]()
        for n in range(3 * (self.bound or 50)):
            if f"k{n}" in self._admitted(eviction, kept, f"k{n}"):
                raise AssertionError("the key just kept was named a victim")

    def test_victims_are_only_keys_it_was_given(self) -> None:
        eviction, kept = self.make_eviction(), set[str]()
        given: set[str] = set()
        for n in range(3 * (self.bound or 50)):
            given.add(f"k{n}")
            if not set(self._admitted(eviction, kept, f"k{n}")) <= given:
                raise AssertionError("a victim was a key never admitted")

    def test_a_forgotten_key_is_never_a_victim_afterwards(self) -> None:
        eviction, kept = self.make_eviction(), set[str]()
        self._admitted(eviction, kept, "forgotten")
        eviction.forgotten("forgotten")
        kept.discard("forgotten")
        for n in range(3 * (self.bound or 50)):
            if "forgotten" in self._admitted(eviction, kept, f"k{n}"):
                raise AssertionError("a forgotten key was named a victim")

    def test_touching_a_key_it_does_not_track_tracks_nothing(self) -> None:
        eviction, kept = self.make_eviction(), set[str]()
        eviction.touched("never-admitted")
        for n in range(3 * (self.bound or 50)):
            if "never-admitted" in self._admitted(eviction, kept, f"k{n}"):
                raise AssertionError("a key only touched was named a victim")

    def test_racing_callers_keep_the_bound_and_evict_each_key_once(self) -> None:
        eviction = self.make_eviction()
        admitted: list[str] = []
        victims: list[str] = []
        lock = threading.Lock()

        def race(racer: int) -> None:
            for n in range(200):
                key = f"r{racer}-{n}"
                named = eviction.admitted(key)
                eviction.touched(key)
                with lock:
                    admitted.append(key)
                    victims.extend(named)

        threads = [threading.Thread(target=race, args=(n,)) for n in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if len(victims) != len(set(victims)):
            raise AssertionError("a key was named a victim twice")
        if self.bound is not None and len(set(admitted) - set(victims)) > self.bound:
            raise AssertionError("racing callers left more than the bound kept")
