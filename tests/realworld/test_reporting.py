"""The questions a ledger is asked in SQL: totals, balances, and a grouping four levels deep,
each checked against SQL written by hand and against the double-entry invariant.
"""

from decimal import Decimal

from sqlalchemy import func, select

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    CountMode,
    Criteria,
    Fold,
    Grouping,
    Level,
)
from sincpro_framework.ddd.model_meta import FieldType, Operator
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .ledger import ZERO, Account, Line, Lines
from .population import Census

POSTED = Condition(field="entry_state", value="posted")

FOUR_LEVELS = Criteria(
    where=POSTED,
    grouping=Grouping(
        by=(
            Level(field="journal_id"),
            Level(field="account_id"),
            Level(field="partner_id"),
            Level(field="posted_at", grain="month"),
        ),
        totals={
            "debit": Fold(function="sum", field="debit"),
            "credit": Fold(function="sum", field="credit"),
        },
        depth=4,
    ),
)


def leaves(buckets, path=()) -> dict[tuple, tuple[int, Decimal]]:
    """The tree flattened to `(journal, account, partner, month) → (count, debit)`."""
    flat = {}
    for bucket in buckets:
        here = path + (bucket.value,)
        if bucket.groups:
            flat.update(leaves(bucket.groups, here))
        else:
            flat[here] = (bucket.count, Decimal(bucket.totals["debit"] or 0))
    return flat


def month_of(database: Database, column):
    """The same month cut the grouping makes, written by hand for the engine under test."""
    if database.engine.dialect.name == "postgresql":
        return func.to_char(column, "YYYY-MM")
    return func.strftime("%Y-%m", column)


def test_double_entry_holds_over_the_whole_ledger(ledger: Repository, census: Census):
    totals = ledger.totals(
        Line, Criteria(where=POSTED), debit="sum:debit", credit="sum:credit"
    )

    assert totals["debit"] == totals["credit"]
    assert totals["debit"] > 0
    assert (
        ledger.count(Line, Criteria(where=POSTED, count=CountMode.EXACT)).value < census.lines
    )


def test_every_account_balance_is_the_sum_of_its_posted_lines(
    ledger: Repository, masters: dict[str, list]
):
    for account in masters["accounts"]:
        totals = ledger.totals(
            Line,
            Criteria(
                where=All(all=[Condition(field="account_id", value=account.id), POSTED])
            ),
            debit="sum:debit",
            credit="sum:credit",
        )
        stored = ledger.get(Account, account.id)

        assert stored is not None
        assert stored.balance == Decimal(totals["debit"] or 0) - Decimal(
            totals["credit"] or 0
        )


def test_the_four_level_grouping_is_the_hand_written_group_by(
    ledger: Repository, database: Database, timed, census: Census
):
    with timed("grouping, four levels", census.lines):
        tree = ledger.group_by_levels(Line, FOUR_LEVELS)

    with ledger.context() as unit:
        month = month_of(database, Line.posted_at)  # type: ignore[attr-defined]
        rows = unit.session.execute(
            select(
                Line.journal_id,  # type: ignore[attr-defined]
                Line.account_id,  # type: ignore[attr-defined]
                Line.partner_id,  # type: ignore[attr-defined]
                month,
                func.count(),
                func.sum(Line.debit),  # type: ignore[attr-defined]
            )
            .where(Line.entry_state == "posted")  # type: ignore[attr-defined]
            .group_by(Line.journal_id, Line.account_id, Line.partner_id, month)  # type: ignore[attr-defined]
        ).all()
    by_hand = {tuple(row[:4]): (row[4], Decimal(row[5])) for row in rows}

    assert leaves(tree) == by_hand
    assert len(tree) == census.journals
    assert sum(bucket.count for bucket in tree) == sum(count for count, _ in by_hand.values())


def test_a_bucket_opens_to_exactly_the_rows_it_counted(ledger: Repository):
    one_level = FOUR_LEVELS.model_copy(
        update={"grouping": FOUR_LEVELS.grouping.model_copy(update={"depth": 1})}
    )

    for bucket in ledger.group_by_levels(Line, one_level):
        opened = ledger.count(
            Line, bucket.criteria.model_copy(update={"count": CountMode.EXACT})
        )
        assert opened.value == bucket.count
        assert not bucket.groups


def test_the_flat_group_by_agrees_with_the_first_level(ledger: Repository):
    flat = ledger.group_by(Line, ["journal_id"], Criteria(where=POSTED))
    first_level = ledger.group_by_levels(
        Line, Criteria(where=POSTED, grouping=Grouping(by=(Level(field="journal_id"),)))
    )

    assert {row["journal_id"]: row["count"] for row in flat} == {
        bucket.value: bucket.count for bucket in first_level
    }


def test_a_null_partner_is_a_bucket_of_its_own_that_opens(ledger: Repository):
    by_partner = ledger.group_by_levels(
        Line, Criteria(where=POSTED, grouping=Grouping(by=(Level(field="partner_id"),)))
    )
    without = next(bucket for bucket in by_partner if bucket.value is None)

    opened = ledger.fetch_all(Lines, without.criteria)

    assert len(opened) == without.count > 0
    assert all(line.partner_id is None for line in opened)


def test_the_definition_says_what_a_line_and_an_account_can_be_asked(ledger: Repository):
    line = describe(Line)
    account = describe(Account)

    assert line.aggregate == "Line" and line.identity == "id"
    assert line.field("posted_at").type is FieldType.DATETIME
    assert line.field("posted_at").accepts(Operator.BETWEEN)
    assert line.field("partner_id").nullable and not line.field("account_id").nullable
    assert line.field("debit").type is FieldType.NUMBER
    assert account.translations["labels"]["balance"] == {"default": "Balance", "es": "Saldo"}
    assert ZERO == Decimal("0.00")
