"""`FrameworkSettings`, opt-in: a project's shared shape inherits it, and the framework reads the
log, OTel, Sentry and release the project set from the project's object — no `config.py` copying
them by hand. A project that does not inherit it changes nothing of the framework's settings.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
import sincpro_log

from sincpro_framework import sincpro_conf
from sincpro_framework.sincpro_conf import FrameworkSettings, SincproConfig, build_config_obj


class SharedSettings(FrameworkSettings):
    environment: str = "TEST"


class QRSettings(SharedSettings):
    timeout: float = 10.0


class ProjectSettings(SharedSettings):
    qr: QRSettings


class NotOptedIn(SincproConfig):
    sentry_dsn: str | None = None
    app_release: str | None = None


@pytest.fixture
def framework(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple]]:
    """The framework's own settings restored after each test; the log reconfiguration recorded
    instead of applied."""
    before = sincpro_conf.settings.model_dump()
    configured: list[tuple] = []
    monkeypatch.setattr(
        sincpro_log,
        "configure_global_logging",
        lambda level, backend, file_path: configured.append((level, backend, file_path)),
    )
    yield configured
    for name, value in before.items():
        setattr(sincpro_conf.settings, name, value)


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "project.yml"
    path.write_text(text)
    return str(path)


def test_what_the_project_set_is_what_the_framework_reads(tmp_path: Path, framework):
    file = write(
        tmp_path,
        "my_project:\n"
        "  sentry_dsn: https://key@sentry.example/1\n"
        "  app_release: my-project:1.2.3\n"
        "  otlp_endpoint: http://collector:4317\n",
    )

    build_config_obj(ProjectSettings, file, "my_project")

    assert sincpro_conf.settings.sentry_dsn == "https://key@sentry.example/1"
    assert sincpro_conf.settings.app_release == "my-project:1.2.3"
    assert sincpro_conf.settings.otlp_endpoint == "http://collector:4317"


def test_what_the_project_left_at_its_default_the_framework_keeps(tmp_path: Path, framework):
    sincpro_conf.settings.otel_service_name = "from-the-framework-environment"
    file = write(tmp_path, "my_project:\n  app_release: my-project:1.2.3\n")

    build_config_obj(ProjectSettings, file, "my_project")

    assert sincpro_conf.settings.otel_service_name == "from-the-framework-environment"
    assert framework == []


def test_a_log_setting_the_project_set_configures_the_log_again(tmp_path: Path, framework):
    file = write(
        tmp_path,
        "my_project:\n  sincpro_framework_log_level: INFO\n  sincpro_framework_log_backend: stdlib\n",
    )

    build_config_obj(ProjectSettings, file, "my_project")

    assert sincpro_conf.settings.sincpro_framework_log_level == "INFO"
    assert framework == [
        ("INFO", "stdlib", sincpro_conf.settings.sincpro_framework_log_file_path)
    ]


def test_a_value_from_the_environment_reaches_the_framework(
    tmp_path: Path, framework, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MY_PROJECT_RELEASE", "my-project:9.9.9")
    file = write(tmp_path, "my_project:\n  app_release: $ENV:MY_PROJECT_RELEASE\n")

    build_config_obj(ProjectSettings, file, "my_project")

    assert sincpro_conf.settings.app_release == "my-project:9.9.9"


def test_the_framework_settings_stay_assignable(tmp_path: Path, framework):
    build_config_obj(
        ProjectSettings, write(tmp_path, "my_project:\n  tenant: t-1\n"), "my_project"
    )

    sincpro_conf.settings.tenant = "t-2"

    assert sincpro_conf.settings.tenant == "t-2"


def test_a_project_not_opted_in_changes_nothing_of_the_framework(tmp_path: Path, framework):
    before = sincpro_conf.settings.model_dump()
    file = write(tmp_path, "my_project:\n  sentry_dsn: https://key@sentry.example/1\n")

    build_config_obj(NotOptedIn, file, "my_project")

    assert sincpro_conf.settings.model_dump() == before
    assert framework == []


def test_the_framework_reads_it_where_it_reads_its_own(tmp_path: Path, framework):
    from sincpro_framework.observability.errors.setup import dsn, tenant

    file = write(
        tmp_path,
        "my_project:\n  sentry_dsn: https://key@glitchtip.example/2\n  tenant: acme\n",
    )

    build_config_obj(ProjectSettings, file, "my_project")

    assert dsn() == "https://key@glitchtip.example/2" and tenant() == "acme"


def test_a_variable_the_project_names_but_nobody_set_leaves_the_framework_its_own(
    tmp_path: Path, framework, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MY_PROJECT_SENTRY", raising=False)
    sincpro_conf.settings.sentry_dsn = "https://key@framework.example/1"
    file = write(tmp_path, "my_project:\n  sentry_dsn: $ENV:MY_PROJECT_SENTRY\n")

    build_config_obj(ProjectSettings, file, "my_project")

    assert sincpro_conf.settings.sentry_dsn == "https://key@framework.example/1"
