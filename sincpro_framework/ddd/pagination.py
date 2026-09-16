"""Which page is being asked for, where the last one ended, and how the next resumes.

Two ways of naming a page, and they are different types rather than one object with both
fields, so an impossible page — a cursor AND an offset — cannot be written down.

`Cursor` is the one this engine is built for: a token names the last row seen instead of a
position, so a row inserted mid-walk changes nothing. `Offset` is what everybody else does,
kept because somebody eventually integrates against this and counts pages; it costs what it
costs, and the cost grows with the offset. See `docs/persistence/reference.md`.

**Nothing here knows SQL.** A cursor mints its token and reads it back; what «past that row»
becomes in a `WHERE` is the adapter's business, and it asks `Pagination.keys_for` for the keys.
"""

import base64
import binascii
import json
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import Field

from sincpro_framework.sincpro_abstractions import DataTransferObject

if TYPE_CHECKING:
    # Only appears in signatures — never in a model field nor at runtime — so a real import
    # buys nothing and closes the cycle: a criteria carries its pagination.
    from sincpro_framework.ddd.criteria import Sort

from sincpro_framework.ddd.exceptions import InvalidCriteria

DEFAULT_LIMIT = 50


_DATETIME_TAG = "__dt__"
_DATE_TAG = "__d__"
_DECIMAL_TAG = "__dec__"


def _tagged(value: Any) -> Any:
    """Marks a key whose type JSON cannot carry on its own.

        in  datetime(2026, 3, 14)  →  out  {'__dt__': '2026-03-14T00:00:00'}
        in  "ds_1"                 →  out  'ds_1'                    already carried

    A key has to come back as the type its column compares against: a datetime returning as
    text would compare lexicographically and move the page boundary.
    """
    if isinstance(value, datetime):
        return {_DATETIME_TAG: value.isoformat()}
    if isinstance(value, date):
        return {_DATE_TAG: value.isoformat()}
    if isinstance(value, Decimal):
        return {_DECIMAL_TAG: str(value)}
    return value


def _untagged(value: Any) -> Any:
    """The inverse of `_tagged`.

    in  {'__dt__': '2026-03-14T00:00:00'}  →  out  datetime(2026, 3, 14)
    """
    if isinstance(value, dict) and _DATETIME_TAG in value:
        return datetime.fromisoformat(value[_DATETIME_TAG])
    if isinstance(value, dict) and _DATE_TAG in value:
        return date.fromisoformat(value[_DATE_TAG])
    if isinstance(value, dict) and _DECIMAL_TAG in value:
        return Decimal(value[_DECIMAL_TAG])
    return value


class CursorKeys(DataTransferObject):
    """The keys of a page's last row and the ordering they belong to.

    The machinery behind the `Cursor` strategy, not something a caller builds: it mints the
    token and reads one back. Compiling «past that row» into a clause is the adapter's job,
    because that is the half that changes with the engine.

    The ordering travels with the keys because a cursor is only meaningful under the sort that
    produced it: reused under another one it names a position with no answer, and refusing is
    the only honest response.
    """

    keys: tuple[Any, ...]
    ordering: str

    @classmethod
    def signature(cls, sorts: tuple["Sort", ...]) -> str:
        """Context: the *effective* ordering, tiebreaker included — what a cursor is stamped
        with, and what reading one is checked against.

        >>> CursorKeys.signature(parse_order("-registered_at,dataset_id"))
        '-registered_at,dataset_id'
        """
        return ",".join(str(sort) for sort in sorts)

    @classmethod
    def of(cls, record: Any, sorts: tuple["Sort", ...]) -> "CursorKeys":
        """Where a page ended, read off its last row.

        >>> CursorKeys.of(Thing(thing_id="th_9", size=3), parse_order("size,-thing_id"))
        CursorKeys(keys=(3, 'th_9'), ordering='size,-thing_id')
        """
        return cls(
            keys=tuple(getattr(record, sort.field) for sort in sorts),
            ordering=cls.signature(sorts),
        )

    @classmethod
    def read(cls, token: str, ordering: str) -> "CursorKeys":
        """A token back into its keys, or a refusal naming which half went wrong.

            in      "eyJrIjpbImEiXSwibyI6Ii1pZCJ9", expecting ordering "-id"
            decode  {"k": ["a"], "o": "-id"}
            out     CursorKeys(keys=('a',), ordering='-id')

        1. Pad and decode; one that will not parse is a corrupted or hand-edited URL.
        2. Final: refuse one minted under another ordering — the caller's fix is to drop it,
           not to retry.

        >>> CursorKeys.read("eyJrIjpbImEiXSwibyI6Ii1pZCJ9", "-id")
        CursorKeys(keys=('a',), ordering='-id')
        >>> CursorKeys.read("eyJrIjpbImEiXSwibyI6Ii1pZCJ9", "-row_count")
        InvalidCriteria: this cursor belongs to a different ordering
        """
        padded = token + "=" * (-len(token) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(padded))
            keys, minted_under = payload["k"], payload["o"]
        except (ValueError, KeyError, TypeError, binascii.Error) as error:
            raise InvalidCriteria(f"cursor is not readable: {error}") from error

        if minted_under != ordering:
            raise InvalidCriteria(
                "this cursor belongs to a different ordering; start from the first page"
            )
        return cls(keys=tuple(_untagged(key) for key in keys), ordering=ordering)

    def token(self) -> str:
        """Context: opaque so it stays ours to change, and unpadded because `=` is escaped by
        some clients and not others — which would turn one cursor into two strings.

        >>> CursorKeys(keys=("a",), ordering="-id").token()
        'eyJrIjpbImEiXSwibyI6Ii1pZCJ9'
        """
        payload = json.dumps(
            {"k": [_tagged(key) for key in self.keys], "o": self.ordering},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


class Offset(DataTransferObject):
    """Locating a page by counting rows from the start. **What everybody else does.**

        in      Offset(rows=160)                    the third page of eighty
        out     the rows after skipping 160

    Here because an integration eventually counts pages, and because a strategy that exists is
    better than one that gets bolted on. Honest about its cost: the database still walks the
    skipped rows, and a row inserted mid-walk shifts everything after it — which is the bug
    `Cursor` does not have.
    """

    rows: int = 0

    def keys_for(self, sorts: tuple["Sort", ...]) -> CursorKeys | None:
        """Nothing to resume past: this strategy counts rows instead of naming one."""
        return None

    def skipped(self) -> int:
        """How many rows the database walks before the page begins.

        >>> Offset(rows=160).skipped()
        160
        """
        return self.rows

    def next_from(self, record: Any, sorts: tuple["Sort", ...]) -> str | None:
        """No token: the caller that counts rows already knows its next count."""
        return None


class Cursor(DataTransferObject):
    """Locating a page by where the previous one ended. **The strategy this engine implements.**

        in      Cursor()                          the first page; nothing came before it
        in      Cursor(token="eyJrIjo…")          the next one, resumed from that row
        out     the rows past that row, in the criteria's own order

    A row inserted or deleted mid-walk changes nothing, because the cursor names a ROW and not
    a position. The token is minted from the last row of the page before; `keys_for` turns it
    back into the keys it holds, checked against the ordering in play.
    """

    token: str | None = None

    def keys_for(self, sorts: tuple["Sort", ...]) -> CursorKeys | None:
        """The row this page starts past, or `None` for the first page.

        in      Cursor(token="eyJrIjpbMywidGhfOSJdfQ"), (size, -thing_id)
        out     CursorKeys(keys=(3, 'th_9'), ordering='size,-thing_id')
        """
        if self.token is None:
            return None
        return CursorKeys.read(self.token, CursorKeys.signature(sorts))

    def skipped(self) -> int:
        """Nothing is skipped: a keyset page starts at a row, not at a count."""
        return 0

    def next_from(self, record: Any, sorts: tuple["Sort", ...]) -> str | None:
        """Where the NEXT page starts, minted from the last row of this one.

        in      the last Thing of the page, (size, -thing_id)
        out     'eyJrIjpbMywidGhfOSJdfQ'
        """
        return CursorKeys.of(record, sorts).token()


class Pagination(DataTransferObject):
    """How many, and from where.

        in      Pagination(limit=80)                              the first eighty
        in      Pagination(limit=80, strategy=Cursor(token="eyJ…"))   the next eighty
        in      Pagination(limit=80, strategy=Offset(rows=160))     the third page of eighty

    Two separate things, and that is the point: **how many belongs to the page, from where is a
    STRATEGY.** Cursor is one strategy and not the object — a caller that switches to counting
    rows changes the strategy, not the way it asks for a page.

    `limit` has a default and no ceiling. A framework says what happens when nobody chooses;
    it does not decide for the application how much it may ask for.
    """

    limit: int = Field(default=DEFAULT_LIMIT, ge=0)
    strategy: Cursor | Offset = Field(default_factory=lambda: Cursor())

    @property
    def asked(self) -> bool:
        """Whether a page was asked for at all, as opposed to the default filling in.

        What was said is read off the fields that were set, so an explicit `limit=50` counts
        although 50 is also the default.

        >>> Pagination().asked, Pagination.model_validate({"limit": 50}).asked
        (False, True)
        """
        return "limit" in self.model_fields_set

    @property
    def cursor(self) -> str | None:
        """The token this page resumes from, or `None` — including when the strategy counts
        rows instead, which resumes from no row at all.

        >>> Pagination(strategy=Cursor(token="eyJ…")).cursor
        'eyJ…'
        >>> Pagination(strategy=Offset(rows=160)).cursor is None
        True
        """
        return self.strategy.token if isinstance(self.strategy, Cursor) else None

    def keys_for(self, sorts: tuple["Sort", ...]) -> CursorKeys | None:
        """The row this page starts past, however its strategy says so. **The adapter asks
        this and never a cursor**: which strategy is in play is the pagination's business.

            Cursor(token="eyJ…")  →  CursorKeys((3, 'th_9'), 'size,-thing_id')
            Cursor()               →  None
            Offset(rows=160)       →  None, and 160 rows skipped instead
        """
        return self.strategy.keys_for(sorts)

    def skipped(self) -> int:
        """How many rows to walk past before the page begins. Zero under any strategy that
        names a row instead of counting."""
        return self.strategy.skipped()

    def next_from(self, record: Any, sorts: tuple["Sort", ...]) -> str | None:
        """The token the next page resumes from, minted from the last row of this one — or
        `None` under a strategy that needs no token.

        >>> Pagination().next_from(last_thing, sorts)
        'eyJrIjpbMywidGhfOSJdfQ'
        """
        return self.strategy.next_from(record, sorts)
