"""The cases both evaluators have to answer the same way, written down.

There are two implementations of the filter language: `ddd/evaluate.py`, which answers it over
records in hand, and the SQL translator, which answers it over a table. A third one now exists
in TypeScript, in the client package. **Nothing stops the three from drifting** — an operator
whose meaning shifts by one row, a `NULL` treated as a value, a list compared by identity —
and the drift shows up as a number that is quietly different, not as a failure.

So the cases live in a file instead of in one suite: this module writes
`tests/ddd/criteria-parity.json`, right beside the tests for `evaluate.py` since that is the
module it is holding to a promise, checks that the in-memory evaluator answers every one of
them, and the client repository copies the same file into its own suite and runs it through
its own engine. A case is added here once and both sides are held to it.

Regenerating is `make criteria-parity`, and the file is committed: a file generated at test
time proves only that the code agrees with itself.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sincpro_framework.ddd.criteria import expression_from
from sincpro_framework.ddd.evaluate import matches

PARITY = Path(__file__).resolve().parent / "criteria-parity.json"


@dataclass
class Row:
    """A record with one of every shape the translator has a rule for: a text, a number, a
    nullable column, a list column and a date written the way JSON carries one."""

    id: str
    name: str
    size: int
    tags: list[str] = field(default_factory=list)
    owner: str | None = None
    made_at: str = "2026-01-01T00:00:00Z"


ROWS = [
    Row(
        id="a",
        name="labs.csv",
        size=300,
        tags=["raw", "urgent"],
        owner="ana",
        made_at="2026-01-10T00:00:00Z",
    ),
    Row(
        id="b",
        name="sales.csv",
        size=120,
        tags=["raw"],
        owner="beto",
        made_at="2026-02-04T00:00:00Z",
    ),
    Row(
        id="c", name="draft.csv", size=0, tags=[], owner=None, made_at="2026-02-18T00:00:00Z"
    ),
    Row(
        id="d",
        name="stock.csv",
        size=900,
        tags=["clean"],
        owner="ana",
        made_at="2026-03-02T00:00:00Z",
    ),
    Row(
        id="e",
        name="LABS.TXT",
        size=120,
        tags=["urgent"],
        owner=None,
        made_at="2026-03-27T00:00:00Z",
    ),
]

CASES: list[dict[str, Any]] = [
    {"name": "equality", "where": {"field": "owner", "operator": "=", "value": "ana"}},
    {"name": "inequality", "where": {"field": "owner", "operator": "!=", "value": "ana"}},
    {
        "name": "a null is not a value, so it answers neither",
        "where": {"field": "owner", "operator": "!=", "value": "beto"},
    },
    {"name": "is null", "where": {"field": "owner", "operator": "is null", "value": True}},
    {
        "name": "is not null",
        "where": {"field": "owner", "operator": "is null", "value": False},
    },
    {"name": "greater than", "where": {"field": "size", "operator": ">", "value": 120}},
    {"name": "greater or equal", "where": {"field": "size", "operator": ">=", "value": 120}},
    {"name": "less than", "where": {"field": "size", "operator": "<", "value": 120}},
    {"name": "less or equal", "where": {"field": "size", "operator": "<=", "value": 120}},
    {
        "name": "zero is a value, not an absence",
        "where": {"field": "size", "operator": "=", "value": 0},
    },
    {
        "name": "between, both ends included",
        "where": {"field": "size", "operator": "between", "value": [120, 300]},
    },
    {
        "name": "in",
        "where": {"field": "name", "operator": "in", "value": ["labs.csv", "stock.csv"]},
    },
    {
        "name": "not in leaves the nulls out",
        "where": {"field": "owner", "operator": "not in", "value": ["ana"]},
    },
    {
        "name": "like is case-insensitive and partial",
        "where": {"field": "name", "operator": "like", "value": "labs"},
    },
    {
        "name": "contains asks about one member of a list",
        "where": {"field": "tags", "operator": "contains", "value": "raw"},
    },
    {
        "name": "not contains",
        "where": {"field": "tags", "operator": "not contains", "value": "raw"},
    },
    {
        "name": "the empty-list question",
        "where": {"field": "tags", "operator": "=", "value": []},
    },
    {
        "name": "the empty-list question, negated",
        "where": {"field": "tags", "operator": "!=", "value": []},
    },
    {
        "name": "a date compared as text, the way JSON carries it",
        "where": {"field": "made_at", "operator": ">=", "value": "2026-03-01T00:00:00Z"},
    },
    {
        "name": "and",
        "where": {
            "all": [
                {"field": "size", "operator": ">", "value": 100},
                {"field": "owner", "operator": "=", "value": "ana"},
            ]
        },
    },
    {
        "name": "or",
        "where": {
            "any": [
                {"field": "owner", "operator": "=", "value": "beto"},
                {"field": "size", "operator": ">", "value": 500},
            ]
        },
    },
    {
        "name": "not",
        "where": {"negate": {"field": "owner", "operator": "=", "value": "ana"}},
    },
    {
        "name": "not over a null: an absence is not the value, and is not its negation either",
        "where": {"negate": {"field": "owner", "operator": "is null", "value": True}},
    },
    {
        "name": "three levels deep",
        "where": {
            "all": [
                {"field": "size", "operator": ">", "value": 0},
                {
                    "any": [
                        {"field": "tags", "operator": "contains", "value": "urgent"},
                        {"negate": {"field": "owner", "operator": "is null", "value": True}},
                    ]
                },
            ]
        },
    },
    {"name": "no filter answers everything", "where": None},
]


def _answered(case: dict[str, Any]) -> list[str]:
    expression = None if case["where"] is None else expression_from(case["where"])
    return [row.id for row in ROWS if matches(row, expression)]


def test_every_case_is_answered_by_the_in_memory_evaluator():
    """The fixtures say what each case answers, and this is what says so. A case whose expected
    ids are wrong fails here before it can teach the client something untrue."""
    written = json.loads(PARITY.read_text()) if PARITY.exists() else None
    assert written is not None, "run `make criteria-parity` to write it"

    assert [one["name"] for one in written["cases"]] == [one["name"] for one in CASES]
    assert written["rows"] == [asdict(row) for row in ROWS]

    for case, saved in zip(CASES, written["cases"]):
        assert saved["expected"] == _answered(case), case["name"]


def test_the_cases_cover_every_operator_the_engine_declares():
    """A new operator with no case is a new way for the two engines to disagree in silence."""
    from sincpro_framework.ddd.criteria import Operator, conditions_of

    asked = {
        condition.operator
        for case in CASES
        if case["where"] is not None
        for condition in conditions_of(expression_from(case["where"]))
    }

    assert asked == set(Operator), f"no parity case for {sorted(set(Operator) - asked)}"


def test_a_case_that_answers_nothing_or_everything_is_still_a_case():
    """Both ends are worth pinning: they are where an empty `all` and an empty `any` go wrong."""
    answers = {case["name"]: _answered(case) for case in CASES}

    assert answers["no filter answers everything"] == [row.id for row in ROWS]
    assert answers["the empty-list question"] == ["c"]


def write_parity() -> Path:
    """Writes the file both repositories read. Called by `make criteria-parity`."""
    PARITY.parent.mkdir(parents=True, exist_ok=True)
    PARITY.write_text(
        json.dumps(
            {
                "about": (
                    "The cases the filter language must answer the same way in every engine. "
                    "Written by the Python suite (tests/ddd/test_criteria_parity.py) and read "
                    "by both: the in-memory evaluator here and the TypeScript engine in "
                    "@sincpro/criteria. Do not edit by hand."
                ),
                "rows": [asdict(row) for row in ROWS],
                "cases": [
                    {"name": one["name"], "where": one["where"], "expected": _answered(one)}
                    for one in CASES
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return PARITY


if __name__ == "__main__":
    print(f"wrote {write_parity()}")
