"""The questions a ledger is asked in SQL: measures, balances, and a grouping four levels deep,
each checked against SQL written by hand and against the double-entry invariant.
"""

from decimal import Decimal

from sqlalchemy import func, select

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Measure,
    parse_order,
)
from sincpro_framework.ddd.model_meta import FieldType, Operator
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .ledger import ZERO, Account, Line, Lines
from .population import Census

POSTED = Condition(field="entry_state", value="posted")

FOUR_LEVELS = Criteria(
    where=POSTED,
    grouping=Grouping(
        group_by=(
            Level(field="journal_id"),
            Level(field="account_id"),
            Level(field="partner_id"),
            Level(field="posted_at", grain="month"),
        ),
        measures={
            "debit": Measure(function="sum", field="debit"),
            "credit": Measure(function="sum", field="credit"),
        },
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
            flat[here] = (bucket.count, Decimal(bucket.measures["debit"] or 0))
    return flat


def month_of(database: Database, column):
    """The same month cut the grouping makes, written by hand for the engine under test."""
    if database.engine.dialect.name == "postgresql":
        return func.to_char(column, "YYYY-MM")
    return func.strftime("%Y-%m", column)


def test_double_entry_holds_over_the_whole_ledger(ledger: Repository, census: Census):
    measures = ledger.measures(
        Line, Criteria(where=POSTED), debit=("sum", "debit"), credit=("sum", "credit")
    )

    assert measures["debit"] == measures["credit"]
    assert measures["debit"] > 0
    assert (
        ledger.count(Line, Criteria(where=POSTED, count=CountMode.EXACT)).value < census.lines
    )


def test_every_account_balance_is_the_sum_of_its_posted_lines(
    ledger: Repository, masters: dict[str, list]
):
    for account in masters["accounts"]:
        measures = ledger.measures(
            Line,
            Criteria(
                where=All(all=[Condition(field="account_id", value=account.id), POSTED])
            ),
            debit=("sum", "debit"),
            credit=("sum", "credit"),
        )
        stored = ledger.get(Account, account.id)

        assert stored is not None
        assert stored.balance == Decimal(measures["debit"] or 0) - Decimal(
            measures["credit"] or 0
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
        update={
            "grouping": FOUR_LEVELS.grouping.model_copy(
                update={"group_by": FOUR_LEVELS.grouping.group_by[:1]}
            )
        }
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
        Line, Criteria(where=POSTED, grouping=Grouping(group_by=(Level(field="journal_id"),)))
    )

    assert {row["journal_id"]: row["count"] for row in flat} == {
        bucket.value: bucket.count for bucket in first_level
    }


def test_a_null_partner_is_a_bucket_of_its_own_that_opens(ledger: Repository):
    by_partner = ledger.group_by_levels(
        Line, Criteria(where=POSTED, grouping=Grouping(group_by=(Level(field="partner_id"),)))
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


def test_two_levels_with_a_page_of_eighty_ids_per_group_match_a_hand_written_window(
    ledger: Repository, timed, census: Census
):
    """Journal → account, the eighty most recent lines of every account as ids: what a screen
    that lists groups and lets the user open one needs, in three statements."""
    asked = Criteria(
        where=POSTED,
        order=parse_order("-posted_at"),
        pagination=Pagination(limit=80),
        grouping=Grouping(group_by=(Level(field="journal_id"), Level(field="account_id"))),
    )

    with timed("grouping, two levels, 80 ids per group", census.lines):
        tree = ledger.group_by_levels(Line, asked)

    with ledger.context() as unit:
        position = (
            func.row_number()
            .over(
                partition_by=[Line.journal_id, Line.account_id],  # type: ignore[attr-defined]
                order_by=[Line.posted_at.desc(), Line.id.desc()],  # type: ignore[attr-defined]
            )
            .label("n")
        )
        inner = (
            select(Line.journal_id, Line.account_id, Line.id, position)  # type: ignore[attr-defined]
            .where(Line.entry_state == "posted")  # type: ignore[attr-defined]
            .subquery()
        )
        rows = unit.session.execute(
            select(inner)
            .where(inner.c.n <= 80)
            .order_by(inner.c.journal_id, inner.c.account_id, inner.c.n)
        ).all()
    by_hand: dict[tuple, list] = {}
    for journal, account, line_id, _ in rows:
        by_hand.setdefault((journal, account), []).append(line_id)

    for journal in tree:
        assert journal.ids == []
        for account in journal.groups:
            assert account.ids == by_hand[(journal.value, account.value)]
            assert (account.cursor is not None) == (account.count > 80)
    opened = ledger.browse(Line, tree[0].groups[0].ids)
    assert opened.ids == tree[0].groups[0].ids


def test_a_pivot_of_journals_by_month_matches_a_hand_written_group_by(
    ledger: Repository, database: Database, timed, census: Census
):
    """The report a ledger is opened for: journals down, months across, the debit in the cell.
    Four statements, whatever the volume."""
    with timed("pivot, journals by month", census.lines):
        matrix = ledger.pivot(
            Line,
            rows=["journal_id"],
            columns=[Level(field="posted_at", grain="month")],
            criteria=Criteria(where=POSTED),
            debit=("sum", "debit"),
        )

    with ledger.context() as unit:
        month = month_of(database, Line.posted_at)  # type: ignore[attr-defined]
        rows = unit.session.execute(
            select(Line.journal_id, month, func.count(), func.sum(Line.debit))  # type: ignore[attr-defined]
            .where(Line.entry_state == "posted")  # type: ignore[attr-defined]
            .group_by(Line.journal_id, month)  # type: ignore[attr-defined]
        ).all()

    by_hand = {
        (journal, when): (count, Decimal(debit)) for journal, when, count, debit in rows
    }
    assert len(matrix.cells) == len(by_hand)
    for cell in matrix.cells:
        count, debit = by_hand[(cell.row[0], cell.column[0])]
        assert cell.count == count and Decimal(cell.measures["debit"]) == debit
    assert matrix.total.count == sum(count for count, _ in by_hand.values())
    assert Decimal(matrix.total.measures["debit"]) == sum(
        (debit for _, debit in by_hand.values()), Decimal(0)
    )
    assert len(matrix.rows) == census.journals


def test_the_heaviest_accounts_come_first_and_only_the_top_ones(ledger: Repository):
    """What a dashboard asks: the five accounts that moved the most, and nothing else."""
    top = Criteria(
        where=POSTED,
        grouping=Grouping(
            group_by=(Level(field="account_id"),),
            measures={"debit": Measure(function="sum", field="debit")},
            where_measures=Condition(field="count", value=10, operator=Operator.GT),
            order=parse_order("-debit"),
            pagination=Pagination(limit=5),
        ),
    )

    buckets = ledger.group_by_levels(Line, top)

    assert len(buckets) == 5
    debits = [Decimal(bucket.measures["debit"]) for bucket in buckets]
    assert debits == sorted(debits, reverse=True)
    assert all(bucket.count > 10 for bucket in buckets)
    every = ledger.group_by_levels(
        Line,
        Criteria(
            where=POSTED,
            grouping=Grouping(
                group_by=(Level(field="account_id"),),
                measures={"debit": Measure(function="sum", field="debit")},
                order=parse_order("-debit"),
            ),
        ),
    )
    assert [one.value for one in buckets] == [one.value for one in every[:5]]


def test_a_repository_narrowed_to_one_journal_never_sees_another(
    ledger: Repository, masters: dict[str, list]
):
    sales, purchases = masters["journals"][0], masters["journals"][1]
    only_sales = ledger.narrowed(
        Criteria(where=Condition(field="journal_id", value=sales.id))
    )

    assert (
        only_sales.count(Lines).value
        == ledger.count(
            Lines, Criteria(where=Condition(field="journal_id", value=sales.id))
        ).value
    )
    assert only_sales.count(Lines).value < ledger.count(Lines).value
    assert only_sales.distinct(Lines, "journal_id") == [sales.id]
    theirs = ledger.first(
        Lines, Criteria(where=Condition(field="journal_id", value=purchases.id))
    )
    assert theirs is not None and only_sales.get(Line, theirs.id) is None
