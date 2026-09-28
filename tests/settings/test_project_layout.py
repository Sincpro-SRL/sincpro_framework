"""The project layout PRD_10 recommends, written to disk and imported: one `settings/` package
outside the contexts, a shape per context in it, the singleton built there — and a context whose
`__init__` builds its bus and imports the settings, with no circular import either way round.
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

FILES = {
    "conf/payments.yml": """
payments_layout:
  environment: TEST
  qr:
    linkser_endpoint: https://linkser.example
  cybersource:
    merchant_id: M-1
    environment: PROD
""",
    "__init__.py": "",
    "settings/shared.py": """
from sincpro_framework.sincpro_conf import SincproConfig


class SharedSettings(SincproConfig):
    environment: str = "TEST"
""",
    "settings/qr.py": """
from .shared import SharedSettings


class QRSettings(SharedSettings):
    linkser_endpoint: str = ""
    timeout: float = 10.0
""",
    "settings/cybersource.py": """
from .shared import SharedSettings


class CybersourceSettings(SharedSettings):
    merchant_id: str = ""
""",
    "settings/payments.py": """
from .cybersource import CybersourceSettings
from .qr import QRSettings
from .shared import SharedSettings


class PaymentsSettings(SharedSettings):
    qr: QRSettings
    cybersource: CybersourceSettings
""",
    "settings/__init__.py": """
from pathlib import Path

from sincpro_framework.sincpro_conf import build_config_obj

from .payments import PaymentsSettings

FILE = str(Path(__file__).parent.parent / "conf" / "payments.yml")

settings = build_config_obj(PaymentsSettings, FILE, "payments_layout")
""",
    "apps/__init__.py": "",
    "apps/qr/__init__.py": """
from sincpro_framework import DataTransferObject, Feature, UseFramework

from payments_layout.settings import settings
from payments_layout.settings.qr import QRSettings


class QRDependencyContextType:
    settings: QRSettings


class CommandWhereIsLinkser(DataTransferObject):
    pass


class ResponseWhereIsLinkser(DataTransferObject):
    endpoint: str
    environment: str


qr = UseFramework("payments-layout-qr", log_after_execution=False)
qr.add_dependency("settings", settings.qr)


@qr.feature(CommandWhereIsLinkser)
class WhereIsLinkser(Feature, QRDependencyContextType):
    def execute(self, dto: CommandWhereIsLinkser) -> ResponseWhereIsLinkser:
        return ResponseWhereIsLinkser(
            endpoint=self.settings.linkser_endpoint, environment=self.settings.environment
        )
""",
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "payments_layout"
    for relative, source in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield root
    for name in [one for one in sys.modules if one.startswith("payments_layout")]:
        del sys.modules[name]


def test_a_context_imported_first_builds_its_bus_over_the_settings(project: Path):
    context = importlib.import_module("payments_layout.apps.qr")

    answer = context.qr(context.CommandWhereIsLinkser(), context.ResponseWhereIsLinkser)

    assert answer.endpoint == "https://linkser.example" and answer.environment == "TEST"


def test_the_settings_imported_first_are_the_object_the_context_holds(project: Path):
    settings = importlib.import_module("payments_layout.settings").settings
    context = importlib.import_module("payments_layout.apps.qr")

    assert context.qr.deps.settings is settings.qr
    assert settings.cybersource.environment == "PROD"
