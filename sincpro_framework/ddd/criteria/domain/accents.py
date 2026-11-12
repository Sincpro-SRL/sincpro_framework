"""Text read without case or accents: `Impresión` and `impresion` are the same word to a search.

The table is the Latin letters that carry one diacritic, each mapped to the plain letter it is
written with (`é` → `e`, `ñ` → `n`, `ç` → `c`). It is built once from the Unicode decomposition
and read in three places, which therefore cannot disagree:

- the in-memory evaluator (`fold`),
- SQLite, where `Database` registers `fold` as the function `sincpro_fold`,
- PostgreSQL and DuckDB, where `translate` replaces the same letters, from and to two strings
  of the same length that this module writes.

A letter with no plain form (`ß`, `ø`, `ł`) is left as it is on every engine.
"""

import unicodedata

FOLDING_FUNCTION = "sincpro_fold"
"""The name SQLite is given for `fold`, so a translator can call it wherever it is not `translate`."""


def _plain_letter(char: str) -> str | None:
    """The ASCII letter a lowercase character is written with, or `None` when it has none.

    in  "é"  →  "e"        decomposes to `e` and a combining acute
    in  "ß"  →  None       no decomposition
    in  "æ"  →  None       a letter of its own, not a letter with a mark
    """
    decomposed = unicodedata.normalize("NFD", char)
    base, marks = decomposed[0], decomposed[1:]
    if (
        marks
        and base.isascii()
        and base.isalpha()
        and all(unicodedata.combining(m) for m in marks)
    ):
        return base
    return None


PLAIN: dict[str, str] = {}
"""Each lowercase Latin letter with a diacritic, and the plain letter it reads as. Lowercase
only: `fold` lowers the text first, so an uppercase accent never reaches the table."""

for _code in range(0x00C0, 0x0250):
    _char = chr(_code)
    if _char.islower() and (_plain := _plain_letter(_char)) is not None:
        PLAIN[_char] = _plain

FROM_LETTERS = "".join(PLAIN)
TO_LETTERS = "".join(PLAIN[char] for char in PLAIN)
"""The two strings `translate` reads: `FROM_LETTERS[i]` becomes `TO_LETTERS[i]`, the same length."""


def fold(text: str) -> str:
    """The text lowercased, with its accents taken off.

    fold("Impresión")   ->  "impresion"
    fold("Año NUEVO")   ->  "ano nuevo"
    fold("ß")           ->  "ß"            no plain letter, so unchanged
    """
    return "".join(PLAIN.get(char, char) for char in text.lower())
