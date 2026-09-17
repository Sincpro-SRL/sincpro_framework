"""Without SQLAlchemy, the vocabulary still imports and the adapter names its extra.

The split the persistence layer is built on holds only while no import crosses the line: one
`from sqlalchemy import …` under `ddd/` and every service that only speaks `Criteria` starts
needing a database driver. A fresh interpreter with the module blocked is the honest check.
"""

import subprocess
import sys
from pathlib import Path

PROGRAM = r"""
import sys

sys.modules["sqlalchemy"] = None

from sincpro_framework.ddd import EntityCollection, Criteria, Entity, Pagination, matches
from sincpro_framework.ddd.criteria import Condition, Operator

page = EntityCollection(items=("a", "b"))
asked = Criteria(where=Condition(field="size", operator=Operator.GT, value=1), pagination=Pagination(limit=2))
assert Criteria.model_validate_json(asked.model_dump_json()) == asked
assert page.filtered_by(None).ids == ["a", "b"]

try:
    import sincpro_framework.orm
except ImportError as error:
    assert "sincpro-framework[sqlalchemy]" in str(error), error
    print("ok")
else:
    raise AssertionError("the adapter imported without SQLAlchemy")
"""


def test_the_vocabulary_needs_no_database_and_the_adapter_names_its_extra():
    result = subprocess.run(
        [sys.executable, "-c", PROGRAM],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout
