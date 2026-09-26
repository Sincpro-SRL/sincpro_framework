"""What a project already running on this framework keeps, untouched, beside the persistence
and events layers: the bus, its dependencies, Features and ApplicationServices, and the async
facade, with nothing new imported, no SQLAlchemy needed and not one warning raised.

A fresh interpreter, because the claim is about what importing the framework does on its
own. Warnings are errors there: a deprecation or an import complaint on the classic path is
a regression, not noise. SQLAlchemy is blocked, because a service that never asked for
persistence must not need it.
"""

import subprocess
import sys
from pathlib import Path

import sincpro_framework

PUBLIC_NAMES_BEFORE_PERSISTENCE = {
    "ApplicationService",
    "DataTransferObject",
    "Feature",
    "UseFramework",
    "logger",
    "TypeDTO",
    "TypeDTOResponse",
}

CLASSIC_SERVICE = r"""
import asyncio
import sys

sys.modules["sqlalchemy"] = None

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandGreet(DataTransferObject):
    name: str


class CommandGreetTwice(DataTransferObject):
    name: str


class ResponseGreeting(DataTransferObject):
    text: str


bus = UseFramework("legacy-service")
bus.add_dependency("greeting", "hello")


@bus.feature(CommandGreet)
class Greet(Feature):
    greeting: str

    def execute(self, dto: CommandGreet) -> ResponseGreeting:
        return ResponseGreeting(text=f"{self.greeting} {dto.name}")


@bus.app_service(CommandGreetTwice)
class GreetTwice(ApplicationService):
    def execute(self, dto: CommandGreetTwice) -> ResponseGreeting:
        once = self.feature_bus.execute(CommandGreet(name=dto.name), ResponseGreeting)
        return ResponseGreeting(text=f"{once.text}, {once.text}")


assert bus(CommandGreet(name="ana"), ResponseGreeting).text == "hello ana"
assert bus(CommandGreetTwice(name="ana"), ResponseGreeting).text == "hello ana, hello ana"
assert asyncio.run(bus.get_async_bus()(CommandGreet(name="bo"), ResponseGreeting)).text == "hello bo"

new_layers = ("sincpro_framework.ddd", "sincpro_framework.orm", "sincpro_framework.events")
loaded = sorted(name for name in sys.modules if name.startswith(new_layers))
assert loaded == [], f"the classic path imported the new layers: {loaded}"
assert "sqlalchemy" not in {name.split(".")[0] for name, module in sys.modules.items() if module}

import sincpro_framework.ddd  # noqa: E402  the vocabulary is there when asked for
import sincpro_framework.events  # noqa: E402

try:
    import sincpro_framework.orm  # noqa: F401
except ImportError as error:
    assert "sincpro-framework[sqlalchemy]" in str(error), error
else:
    raise AssertionError("the adapter imported without SQLAlchemy")

print("classic path ok")
"""


def test_the_public_names_a_project_relied_on_are_still_exported():
    assert PUBLIC_NAMES_BEFORE_PERSISTENCE <= set(sincpro_framework.__all__)
    for name in PUBLIC_NAMES_BEFORE_PERSISTENCE:
        assert hasattr(sincpro_framework, name), name


def test_a_classic_service_runs_without_sqlalchemy_and_without_a_warning():
    """`-W error` turns any warning the classic path raises into a failure, and the blocked
    `sqlalchemy` proves a service that never asked for persistence does not pay for it."""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", CLASSIC_SERVICE],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "classic path ok" in result.stdout
