"""What a client needs to speak the filter language: its schema, and what a dynamic value means.

Two files are written here and committed, and the TypeScript package copies them
(`make criteria-contract`):

- `criteria.schema.json`, the JSON Schema of a `Criteria`, generated from the models so it cannot
  say anything the engine does not.
- `criteria-resolution.json`, the cases of what `{"$": "today"}`, `{"$": "context", …}` and
  the rest read to: each one says what day and zone it is, what the server's context and the call's
  parameters hold, the filter as it is stored, and the filter as it is run.

**The expectations are written by hand, here.** A file produced by the code under test would only
prove the code agrees with itself; these say what a person means by «thirty days ago» and are
checked against the engine, and the client package is held to the same file.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sincpro_framework.ddd.criteria import (
    RESOLVERS,
    Criteria,
    ResolutionContext,
    expression_from,
    resolve,
)

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "criteria.schema.json"
RESOLUTION = HERE / "criteria-resolution.json"

ABOUT = (
    "What a dynamic value reads to. Every case gives the instant, the timezone, the first weekday "
    "(0 is Monday), what the server's context and the call's params hold, the filter as stored "
    "(`where`) and as run (`resolved`). Dates are `YYYY-MM-DD`; instants are UTC `…Z`. Written by "
    "hand in tests/ddd/criteria/test_criteria_contract.py and read by the Python engine and by "
    "@sincpro/criteria. Do not edit by hand."
)


def due(value: Any, operator: str = "<") -> dict[str, Any]:
    return {"field": "due", "operator": operator, "value": value}


def span(**units: int) -> dict[str, Any]:
    return {"$": "today", "plus": units}


def between(start: str, end: str) -> dict[str, Any]:
    return {"all": [due(start, ">="), due(end, "<")]}


def case(
    name: str,
    where: dict[str, Any],
    resolved: dict[str, Any],
    now: str = "2026-10-12T15:30:00Z",
    timezone: str = "UTC",
    week_starts_on: int = 0,
    context: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "now": now,
        "timezone": timezone,
        "week_starts_on": week_starts_on,
        "context": context or {},
        "params": params or {},
        "where": where,
        "resolved": resolved,
    }


CASES: list[dict[str, Any]] = [
    case("today", due({"$": "today"}), due("2026-10-12")),
    case("thirty days ago", due(span(days=-30), ">="), due("2026-09-12", ">=")),
    case("a week ahead", due(span(weeks=1)), due("2026-10-19")),
    case(
        "the first of the month",
        due({"$": "today", "start_of": "month"}),
        due("2026-10-01"),
    ),
    case(
        "the first of the quarter",
        due({"$": "today", "start_of": "quarter"}),
        due("2026-10-01"),
    ),
    case(
        "the first of the year",
        due({"$": "today", "start_of": "year"}),
        due("2026-01-01"),
    ),
    case(
        "the start of the week, Monday first",
        due({"$": "today", "start_of": "week"}, ">="),
        due("2026-10-12", ">="),
    ),
    case(
        "the start of the week, Sunday first",
        due({"$": "today", "start_of": "week"}, ">="),
        due("2026-10-11", ">="),
        week_starts_on=6,
    ),
    case(
        "a month end is clamped",
        due(span(months=1)),
        due("2026-02-28"),
        now="2026-01-31T09:00:00Z",
    ),
    case(
        "a leap year keeps its 29th",
        due(span(months=1)),
        due("2028-02-29"),
        now="2028-01-31T09:00:00Z",
    ),
    case(
        "a move comes before the snap",
        due({"$": "today", "plus": {"months": -1}, "start_of": "month"}),
        due("2026-09-01"),
    ),
    case(
        "now two hours ago",
        {"field": "at", "operator": ">=", "value": {"$": "now", "plus": {"hours": -2}}},
        {"field": "at", "operator": ">=", "value": "2026-10-12T13:30:00Z"},
    ),
    case(
        "the timezone decides the day",
        due({"$": "today"}),
        due("2026-10-12"),
        now="2026-10-13T02:00:00Z",
        timezone="America/La_Paz",
    ),
    case(
        "the start of a day in a timezone is a UTC instant",
        {"field": "at", "operator": ">=", "value": {"$": "now", "start_of": "day"}},
        {"field": "at", "operator": ">=", "value": "2026-10-12T04:00:00Z"},
        now="2026-10-13T02:00:00Z",
        timezone="America/La_Paz",
    ),
    case(
        "a day across a clock change is a day",
        {"field": "at", "operator": ">=", "value": {"$": "now", "plus": {"days": 1}}},
        {"field": "at", "operator": ">=", "value": "2026-09-06T11:00:00Z"},
        now="2026-09-05T12:00:00Z",
        timezone="America/Santiago",
    ),
    case(
        "a value of the server's context",
        {"field": "owner_id", "operator": "=", "value": {"$": "context", "key": "user_id"}},
        {"field": "owner_id", "operator": "=", "value": "u-7"},
        context={"user_id": "u-7"},
    ),
    case(
        "a date of the context, moved and snapped",
        due(
            {
                "$": "context",
                "key": "report_date",
                "plus": {"months": -3},
                "start_of": "month",
            },
            ">=",
        ),
        due("2026-03-01", ">="),
        context={"report_date": "2026-06-15"},
    ),
    case(
        "a parameter is found by a path",
        {
            "field": "total",
            "operator": ">",
            "value": {"$": "param", "name": "steps.order.total"},
        },
        {"field": "total", "operator": ">", "value": 9},
        params={"steps": {"order": {"total": 9}}},
    ),
    case(
        "a dynamic value inside a list",
        {
            "field": "code",
            "operator": "in",
            "value": [{"$": "param", "name": "text"}, "0"],
        },
        {"field": "code", "operator": "in", "value": ["1.2", "0"]},
        params={"text": "1.2"},
    ),
    case(
        "this month is closed at the start and open at the end",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "name": "this_month"},
        },
        between("2026-10-01", "2026-11-01"),
    ),
    case(
        "last month in January is December of the year before",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "name": "last_month"},
        },
        between("2025-12-01", "2026-01-01"),
        now="2026-01-20T09:00:00Z",
    ),
    case(
        "this week, Monday first",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "name": "this_week"},
        },
        between("2026-10-12", "2026-10-19"),
    ),
    case(
        "the next quarter",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "name": "next_quarter"},
        },
        between("2027-01-01", "2027-04-01"),
    ),
    case(
        "the last thirty days leave today out",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "unit": "day", "last": 30},
        },
        between("2026-09-12", "2026-10-12"),
    ),
    case(
        "the last thirty days with today",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "unit": "day", "last": 30, "include_current": True},
        },
        between("2026-09-13", "2026-10-13"),
    ),
    case(
        "the next seven days with today",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "unit": "day", "next": 7, "include_current": True},
        },
        between("2026-10-12", "2026-10-19"),
    ),
    case(
        "the last three months",
        {
            "field": "due",
            "operator": "between",
            "value": {"$": "period", "unit": "month", "last": 3},
        },
        between("2026-07-01", "2026-10-01"),
    ),
    case(
        "a column against another is left for the row",
        {"field": "total", "operator": ">", "value": {"$": "field", "name": "credit_limit"}},
        {"field": "total", "operator": ">", "value": {"$": "field", "name": "credit_limit"}},
    ),
    case(
        "a group keeps its shape around what it reads",
        {
            "all": [
                due({"$": "today"}),
                {"negate": {"field": "state", "operator": "=", "value": "draft"}},
                {
                    "any": [
                        due(span(days=-1), ">="),
                        {"field": "n", "operator": "=", "value": 1},
                    ]
                },
            ]
        },
        {
            "all": [
                due("2026-10-12"),
                {"negate": {"field": "state", "operator": "=", "value": "draft"}},
                {
                    "any": [
                        due("2026-10-11", ">="),
                        {"field": "n", "operator": "=", "value": 1},
                    ]
                },
            ]
        },
    ),
    case(
        "nothing dynamic is left alone",
        {
            "all": [
                due("2026-01-01"),
                {"field": "meta", "operator": "=", "value": {"today": 1}},
            ]
        },
        {
            "all": [
                due("2026-01-01"),
                {"field": "meta", "operator": "=", "value": {"today": 1}},
            ]
        },
    ),
]


EXAMPLES: list[dict[str, Any]] = [
    {
        "where": {
            "all": [
                {"field": "row_count", "operator": ">", "value": 1000},
                {
                    "any": [
                        {"field": "encoding", "operator": "in", "value": ["utf-8", "latin-1"]}
                    ]
                },
            ]
        },
        "order": [{"field": "registered_at", "descending": True}],
        "pagination": {"limit": 100},
        "specification": {"name": {}, "runs": {"pagination": {"limit": 20}}},
        "grouping": {
            "group_by": [{"field": "encoding"}, {"field": "registered_at", "grain": "month"}],
            "measures": {"rows": {"function": "sum", "field": "row_count"}},
        },
        "count": "capped",
        "meta": True,
    },
    {
        "where": {
            "all": [
                {"field": "due", "operator": "<", "value": {"$": "today"}},
                {
                    "field": "owner_id",
                    "operator": "=",
                    "value": {"$": "context", "key": "user_id"},
                },
                {
                    "field": "posted_at",
                    "operator": ">=",
                    "value": {"$": "today", "plus": {"days": -30}, "start_of": "month"},
                },
                {
                    "field": "closed_at",
                    "operator": "between",
                    "value": {"$": "period", "unit": "day", "last": 30},
                },
                {
                    "field": "total",
                    "operator": ">",
                    "value": {"$": "field", "name": "credit_limit"},
                },
            ]
        }
    },
]


def resolution_of(one: dict[str, Any]) -> ResolutionContext:
    return ResolutionContext(
        now=datetime.fromisoformat(one["now"]),
        timezone=ZoneInfo(one["timezone"]),
        week_starts_on=one["week_starts_on"],
        context=one["context"],
        params=one["params"],
    )


def written(expression: Any) -> Any:
    return json.loads(json.dumps(expression.model_dump(mode="json")))


def contract_files() -> dict[Path, str]:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sincpro.com.bo/schemas/criteria.schema.json",
        **Criteria.model_json_schema(mode="validation"),
        "examples": EXAMPLES,
    }
    return {
        SCHEMA: json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        RESOLUTION: json.dumps(
            {
                "about": ABOUT,
                "resolvers": [one.model_dump(mode="json") for one in RESOLVERS],
                "cases": CASES,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    }


@pytest.mark.parametrize("one", CASES, ids=lambda one: one["name"])
def test_a_dynamic_value_reads_to_what_the_contract_says(one: dict[str, Any]):
    stored = expression_from(one["where"])

    run = resolve(stored, resolution_of(one))

    assert written(run) == one["resolved"]


def test_the_cases_cover_every_kind_of_dynamic_value():
    asked = json.dumps([one["where"] for one in CASES])

    for kind in ("today", "now", "period", "context", "param", "field"):
        assert f'"$": "{kind}"' in asked, kind


@pytest.mark.parametrize("example", EXAMPLES)
def test_an_example_of_the_schema_is_a_criteria_the_engine_reads(example: dict[str, Any]):
    read = Criteria.model_validate(example)

    assert read.model_dump(mode="json", exclude_none=True, exclude_defaults=True) is not None
    assert Criteria.model_validate(read.model_dump(mode="json")) == read


@pytest.mark.parametrize("path", [SCHEMA, RESOLUTION], ids=lambda path: path.name)
def test_the_committed_contract_is_what_the_engine_generates(path: Path):
    """If this fails, run `make criteria-contract` and commit what it writes."""
    assert path.read_text() == contract_files()[path]


def write_contract() -> list[Path]:
    for path, text in contract_files().items():
        path.write_text(text)
    return list(contract_files())


if __name__ == "__main__":
    for written_path in write_contract():
        print(f"wrote {written_path}")
