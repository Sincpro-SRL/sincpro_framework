"""The ids the framework mints: UUID v7 as 32 hex, by the standard library from Python 3.14 and by
`rfc9562_uuid7` before it — ordered within a process even when a millisecond's counter fills or
the clock moves back.

The backport is called by its own name, so these run on every Python the framework supports.
"""

import sys
import time
import uuid

from sincpro_framework.common.ids import moment_of, new_entity_id, uuid7
from sincpro_framework.common.ids.uuid7 import rfc9562_uuid7


def test_uuid7_is_the_standard_librarys_from_python_3_14_and_the_backport_before_it():
    if sys.version_info >= (3, 14):
        assert uuid7 is uuid.uuid7
    else:
        assert uuid7 is rfc9562_uuid7
    assert getattr(uuid, "uuid7", None) is not rfc9562_uuid7  # the stdlib is never patched


def test_a_minted_id_is_a_uuid_v7_of_32_lowercase_hex():
    minted = new_entity_id()
    parsed = uuid.UUID(hex=minted)

    assert (parsed.version, parsed.variant) == (7, uuid.RFC_4122)
    assert minted == parsed.hex and len(minted) == 32


def test_ten_thousand_ids_of_one_frozen_millisecond_come_out_in_order(monkeypatch):
    """4096 ids fill the 12-bit counter: the next millisecond is borrowed, never counted back."""
    frozen = time.time_ns()
    monkeypatch.setattr(time, "time_ns", lambda: frozen)

    minted = [rfc9562_uuid7().hex for _ in range(10_000)]

    assert minted == sorted(minted)
    assert len(set(minted)) == 10_000
    assert all(uuid.UUID(hex=one).version == 7 for one in minted)
    first, last = moment_of(minted[0]), moment_of(minted[-1])
    assert first is not None and last is not None and last > first  # borrowed milliseconds


def test_a_clock_moved_back_never_mints_a_smaller_id(monkeypatch):
    now = time.time_ns()
    monkeypatch.setattr(time, "time_ns", lambda: now)
    before = rfc9562_uuid7().hex
    monkeypatch.setattr(time, "time_ns", lambda: now - 5_000_000_000)  # five seconds back

    after = rfc9562_uuid7().hex

    assert after > before
    first, second = moment_of(before), moment_of(after)
    assert first is not None and second is not None and second >= first
