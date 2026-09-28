"""Where each value came from (`describe_settings`), and the optional check that a context reads
only its own section (`settings_scope_violations`) — data both, never an exception."""

from pathlib import Path

import pytest

from sincpro_framework.sincpro_conf import build_config_obj, describe_settings
from sincpro_framework.testing import settings_scope_violations
from tests.settings.test_resolution import DOCUMENT, FlatConfig, PaymentsSettings


@pytest.fixture
def document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LINKSER_ENDPOINT", "https://linkser.example")
    monkeypatch.setenv("CYBERSOURCE_API_SECRET", "s3cr3t")
    path = tmp_path / "payments.yml"
    path.write_text(DOCUMENT)
    return str(path)


def by_path(settings) -> dict:
    return {one.path: one for one in describe_settings(settings)}


# ---------------------------------------------------------------------------------------------
# describe_settings
# ---------------------------------------------------------------------------------------------


def test_every_value_is_described_by_its_path_with_where_it_came_from(document: str):
    described = by_path(build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk"))

    assert described["environment"].source == f"file {document} at sincpro_payments_sdk"
    assert described["qr.environment"].source == "inherited from sincpro_payments_sdk"
    assert described["qr.linkser_endpoint"].source == "env LINKSER_ENDPOINT"
    assert (
        described["qr.timeout"].source == "default" and described["qr.timeout"].value == 10.0
    )
    assert described["cybersource.environment"].source.endswith(
        "sincpro_payments_sdk.cybersource"
    )


def test_a_secret_is_described_masked(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    secret = by_path(settings)["cybersource.api_secret"]

    assert secret.value == "**********" and secret.source == "env CYBERSOURCE_API_SECRET"
    assert all("s3cr3t" not in repr(one) for one in describe_settings(settings))


def test_a_section_handed_to_a_context_describes_itself(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    described = by_path(settings.qr)

    assert described["environment"].source == "inherited from sincpro_payments_sdk"
    assert described["timeout"].source == "default"


def test_a_value_assigned_after_building_is_described_with_its_current_value(document: str):
    flat = build_config_obj(FlatConfig, document, "another_project")
    flat.timeout = 3.0

    described = by_path(flat)

    assert described["log_level"].source.startswith("file ")
    assert described["timeout"].value == 3.0


def test_an_object_built_by_hand_is_described_as_assigned():
    assert {one.source for one in describe_settings(FlatConfig(timeout=2.0))} == {"assigned"}


# ---------------------------------------------------------------------------------------------
# settings_scope_violations
# ---------------------------------------------------------------------------------------------

PROJECT = {
    "__init__.py": "",
    "settings/__init__.py": "settings = None\n",
    "apps/__init__.py": "",
    "apps/qr/__init__.py": "",
    "apps/qr/adapters/linkser.py": (
        "from scope_project.settings import settings\n"
        "\n"
        "endpoint = settings.qr.linkser_endpoint\n"
        "merchant = settings.cybersource.merchant_id\n"
    ),
    "apps/cybersource/__init__.py": "",
    "apps/cybersource/adapters/client.py": (
        "from scope_project.settings import settings\n"
        "\n"
        "merchant = settings.cybersource.merchant_id\n"
        "\n"
        "class Holder:\n"
        "    qr = None\n"
        "\n"
        "unrelated = Holder().qr\n"
    ),
    "entrypoints/report.py": (
        "from scope_project.settings import settings\n"
        "\n"
        "everything = (settings.qr, settings.cybersource)\n"
    ),
}


@pytest.fixture
def scope_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    for relative, source in PROJECT.items():
        path = tmp_path / "scope_project" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    return "scope_project"


def test_a_context_reading_another_contexts_section_is_reported(scope_project: str):
    found = settings_scope_violations(scope_project, PaymentsSettings)

    assert [(one.module, one.line, one.context, one.section) for one in found] == [
        ("scope_project.apps.qr.adapters.linkser", 4, "qr", "cybersource")
    ]
    assert "reads the section 'cybersource'" in repr(found[0])


def test_its_own_section_a_module_outside_every_context_and_other_objects_are_free(
    scope_project: str,
):
    found = settings_scope_violations(scope_project, PaymentsSettings)

    assert not any(one.module.endswith(("client", "report")) for one in found)


def test_a_variable_that_gave_nothing_is_described_as_the_default_it_fell_back_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("FLAT_LEVEL", raising=False)
    monkeypatch.setenv("FLAT_TIMEOUT", "not-a-number")
    path = tmp_path / "flat.yml"
    path.write_text("flat:\n  log_level: $ENV:FLAT_LEVEL\n  timeout: $ENV:FLAT_TIMEOUT\n")

    described = by_path(build_config_obj(FlatConfig, str(path), "flat"))

    assert described["log_level"].source == "default (FLAT_LEVEL not set)"
    assert described["timeout"].source == "default (FLAT_TIMEOUT cannot be used)"
    assert described["timeout"].value == 1.0


def test_a_type_is_described_as_written(document: str):
    described = by_path(build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk"))

    assert described["qr.timeout"].type == "float"
    assert described["cybersource.api_secret"].type == "Secret[str]"
