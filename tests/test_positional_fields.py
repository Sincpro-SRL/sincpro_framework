"""`Run` and `RuntimeUseCase` became DTOs: built by keyword. Until the next major version the
positional form a dataclass took still builds the same object, and warns.

The type checker already refuses the positional form, so the calls below go through `Any` — the
way a consumer's untyped code, written for the dataclass, reaches them.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from sincpro_framework.cron import Run
from sincpro_framework.runtime_use_cases import RuntimeUseCase

NOW = datetime(2026, 9, 28, tzinfo=UTC)
use_case_as_a_dataclass: Any = RuntimeUseCase
run_as_a_dataclass: Any = Run


def test_the_positional_form_builds_the_same_object_and_warns() -> None:
    with pytest.warns(DeprecationWarning, match="name=, source="):
        positional = use_case_as_a_dataclass("quote", "source", 2)
    assert positional == RuntimeUseCase(name="quote", source="source", version=2)
    with pytest.warns(DeprecationWarning):
        run = run_as_a_dataclass("close", NOW, "", started_at=NOW)
    assert run == Run(name="close", scheduled_for=NOW, key="", started_at=NOW)


def test_more_fields_than_it_has_is_refused() -> None:
    with pytest.raises(TypeError):
        use_case_as_a_dataclass("quote", "source", 1, True, None, "one too many")
