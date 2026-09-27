"""`holds`: the in-memory filter over values read however the caller reads them — a column of a
row, a reference into a workflow's results — with the same answers `matches` gives an object.
"""

from sincpro_framework.ddd import Criteria, holds

ROW = {"state": "posted", "total": 12_000, "tags": ["vip"], "partner": None}


def _where(raw: dict) -> Criteria:
    return Criteria.model_validate({"where": raw})


def test_a_condition_reads_its_field_through_the_reader():
    criteria = _where({"field": "total", "operator": ">=", "value": 10_000})

    assert holds(criteria.expression, ROW.get)


def test_all_any_and_not_compose_as_they_do_for_matches():
    criteria = _where(
        {
            "all": [
                {"field": "state", "operator": "=", "value": "posted"},
                {"any": [{"field": "tags", "operator": "contains", "value": "vip"}]},
                {"negate": {"field": "total", "operator": "<", "value": 100}},
            ]
        }
    )

    assert holds(criteria.expression, ROW.get)


def test_a_missing_value_fails_every_operator_but_is_null_as_in_sql():
    assert not holds(
        _where({"field": "partner", "operator": "=", "value": 1}).expression, ROW.get
    )
    assert holds(
        _where({"field": "partner", "operator": "is null", "value": True}).expression, ROW.get
    )


def test_no_filter_holds_for_everything():
    assert holds(None, ROW.get)
