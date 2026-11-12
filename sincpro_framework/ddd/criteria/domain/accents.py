"""Text read without case or accents: `Impresión` and `impresion` are the same word to a search.

The table is the Latin letters that carry a diacritic, in either case, each mapped to the plain
lowercase letter it is written with (`é` and `É` → `e`, `ñ` → `n`, `ç` → `c`). It is built once
from the Unicode decomposition and read in three places, which therefore cannot disagree:

- the in-memory evaluator (`fold`),
- SQLite, where `Database` registers `fold` as the function `sincpro_fold`,
- PostgreSQL and DuckDB, where `translate` replaces the same letters, from and to two strings
  of the same length that this module writes.

Both cases are in the table because a database's `lower()` follows its collation: under `C` it
lowers only ASCII, and `IMPRESIÓN` would keep its `Ó`. A letter with no plain form (`ß`, `ø`,
`ł`) is left as it is on every engine.
"""

import unicodedata

FOLDING_FUNCTION = "sincpro_fold"
"""The name SQLite's connections are given `fold` under, for the translator to call."""


def _plain_letter(char: str) -> str | None:
    """The plain lowercase letter a character is written with, or `None` when it has none.

    in  "é"  →  "e"        decomposes to `e` and a combining acute
    in  "É"  →  "e"        the same letter, uppercase
    in  "ß"  →  None       no decomposition
    in  "æ"  →  None       a letter of its own, not a letter with a mark
    """
    decomposed = unicodedata.normalize("NFD", char)
    base, marks = decomposed[0], decomposed[1:]
    if (
        marks
        and base.isascii()
        and base.isalpha()
        and all(unicodedata.combining(mark) for mark in marks)
    ):
        return base.lower()
    return None


PLAIN: dict[str, str] = {
    letter: plain
    for letter in map(chr, range(0x00C0, 0x0250))
    if (plain := _plain_letter(letter)) is not None
}
"""Each Latin letter with a diacritic, in either case, and the plain lowercase letter it reads as."""

FROM_LETTERS = "".join(PLAIN)
TO_LETTERS = "".join(PLAIN.values())
"""The two strings `translate` reads: `FROM_LETTERS[i]` becomes `TO_LETTERS[i]`, the same length."""


def fold(text: str) -> str:
    """The text with its accents taken off, lowercased.

    fold("Impresión")   ->  "impresion"
    fold("Año NUEVO")   ->  "ano nuevo"
    fold("İstanbul")    ->  "istanbul"
    fold("ß")           ->  "ß"            no plain letter, so unchanged

    The letters are replaced before lowering, as `translate` meets them after a `lower()` that
    may have left them as they were: Python lowers `İ` to two characters, an `i` and a dot.
    """
    return "".join(PLAIN.get(char, char) for char in text).lower()
