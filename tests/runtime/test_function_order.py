"""A file reads top to bottom as building blocks first and the thing that uses them last.

The check runs over the framework's own source: a function defined below the one that uses it is
refused here and not found by a reader who has to scroll down for what a name means.
"""

from pathlib import Path

import pytest

import sincpro_framework
from sincpro_framework.runtime.testing.analysis.function_order import in_dependency_order

PACKAGE = Path(sincpro_framework.__file__).parent
SOURCES = sorted(path for path in PACKAGE.rglob("*.py"))


def test_a_function_that_is_used_moves_above_the_one_that_uses_it():
    source = "def use():\n    return base()\n\n\ndef base():\n    return 1\n"

    assert (
        in_dependency_order(source)
        == "def base():\n    return 1\n\n\ndef use():\n    return base()\n"
    )


def test_methods_follow_the_same_rule_and_the_constructor_stays_first():
    source = (
        "class Thing:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def run(self):\n"
        "        return self._step()\n"
        "\n"
        "    def _step(self):\n"
        "        return self.x\n"
    )

    ordered = in_dependency_order(source)

    assert (
        ordered.index("def __init__") < ordered.index("def _step") < ordered.index("def run")
    )


def test_two_functions_that_call_each_other_are_left_as_they_are():
    source = "def a():\n    return b()\n\n\ndef b():\n    return a()\n"

    assert in_dependency_order(source) == source


def test_a_function_whose_decorator_registers_something_does_not_move():
    source = "def use():\n    return base()\n\n\n@registry.add\ndef base():\n    return 1\n"

    assert in_dependency_order(source) == source


def test_a_function_that_needs_something_declared_in_between_does_not_move():
    source = "def use():\n    return base()\n\n\nDEFAULT = 1\n\n\ndef base(x=DEFAULT):\n    return x\n"

    assert in_dependency_order(source) == source


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_the_framework_is_in_dependency_order(path: Path):
    source = path.read_text()

    assert in_dependency_order(source) == source
