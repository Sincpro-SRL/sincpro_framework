"""The accent table: what `fold` reads a letter as, and that SQL replaces the same letters."""

import pytest

from sincpro_framework.ddd.criteria.domain.accents import (
    FROM_LETTERS,
    PLAIN,
    TO_LETTERS,
    fold,
)


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Impresión", "impresion"),
        ("IMPRESIÓN", "impresion"),
        ("İstanbul", "istanbul"),
        ("Año NUEVO", "ano nuevo"),
        ("Ñandú", "nandu"),
        ("Crème brûlée", "creme brulee"),
        ("ß", "ß"),
        ("Łódź", "łodz"),
    ],
)
def test_fold_lowers_and_takes_the_accents_off(text, folded):
    assert fold(text) == folded


def test_every_accented_letter_reads_as_one_ascii_letter():
    assert all(plain.isascii() and plain.isalpha() for plain in PLAIN.values())


def test_sql_replaces_the_letters_the_fold_replaces_one_for_one():
    """`translate` reads two strings: each from-letter is replaced by the to-letter at its
    position, so they have to be the same length and cover the same letters."""
    assert len(FROM_LETTERS) == len(TO_LETTERS) == len(PLAIN)
    assert dict(zip(FROM_LETTERS, TO_LETTERS)) == PLAIN


def test_an_uppercase_accent_is_in_the_table_too():
    """A database's `lower()` follows its collation, and under `C` it leaves `Ó` as it is: the
    table has to replace the uppercase letter itself."""
    assert PLAIN["Ó"] == PLAIN["ó"] == "o"
    assert PLAIN["Ñ"] == PLAIN["ñ"] == "n"
