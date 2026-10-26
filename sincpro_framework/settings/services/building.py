"""A shape resolved against a project's file and built — and the framework's own settings handed
what a project's shape set of them.

    built = build_settings(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr", None)
    hand_to_the_framework(project_settings, framework_settings)

Context: a flat class, read at a section without a dot, is built exactly as it always was;
nested shapes, the cascade, a dotted path and `env_prefix` are reached only by a project that
writes them.
"""

import os
from collections.abc import Mapping

import sincpro_log

from sincpro_framework.settings.adapters.environment import with_environment_by_path
from sincpro_framework.settings.adapters.yaml_file import load_yaml_file
from sincpro_framework.settings.domain.config import (
    FrameworkSettings,
    SettingSource,
    SincproConfig,
    TypeSincproConfigModel,
    logger,
)
from sincpro_framework.settings.services.resolution import locate, resolve_values


def _store_sources(built: SincproConfig, sources: dict[str, str], prefix: str) -> None:
    """Each section stores where its own values came from, by its own paths — so a context
    handed `settings.qr` describes it as if it had been built alone.

        _store_sources(payments, {"environment": "default", "qr.timeout": "env T"}, "")
            ->  payments._sources == {"environment": "default", "qr.timeout": "env T"}
                payments.qr._sources == {"timeout": "env T"}
    """
    built._sources = {
        path.removeprefix(prefix): source
        for path, source in sources.items()
        if path.startswith(prefix)
    }
    for name in type(built).model_fields:
        value = getattr(built, name)
        if isinstance(value, SincproConfig):
            _store_sources(value, sources, f"{prefix}{name}.")


def build_settings(
    shape: type[TypeSincproConfigModel],
    config_path: str,
    sub_key: str | None,
    sources: Mapping[str, SettingSource] | None,
) -> TypeSincproConfigModel:
    """Build `shape` from the YAML file at `config_path`, read at the section `sub_key` names.

        build_settings(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr", None)
            ->  QRSettings(linkser_endpoint="https://…", environment=Environment.PROD)

    1. The document: the file, and — for a shape declaring `env_prefix` — the environment by path.
    2. The section `sub_key` names, and the sections above it inside the project's own.
    3. The shape's values: its section, its nested shapes, the cascade (`resolve_values`).
    4. Validated whole in one call — every problem one error, by path. A value written
       `$<SOURCE>:name` is read from `sources[SOURCE]` — `$ENV:` needs none.
    Final: the built object, with where each value came from stored for `describe_settings`.
    """
    document = load_yaml_file(config_path)
    prefix = shape.env_prefix
    if prefix:
        anchor = sub_key.split(".")[0] if sub_key else None
        document = with_environment_by_path(document, prefix, anchor, os.environ)
    section, above = locate(document, sub_key, config_path)

    logger.info(f"read yaml file {config_path} for config {shape.__name__}")
    came_from: dict[str, str] = {}
    values = resolve_values(shape, section, sub_key or "", config_path, above, came_from)
    built = shape.model_validate(values, context={"sources": dict(sources or {})})
    _store_sources(built, came_from, "")
    return built


def hand_to_the_framework(built: FrameworkSettings, framework: FrameworkSettings) -> None:
    """What a project's shape set of the framework's own settings, copied into `framework` —
    and the log configured again when a log setting was among them.

    Context: the framework's modules hold its settings object from import time, so the project's
    values reach them by being written into it; a value the project left at its default is not
    written, so the framework's own environment still decides it.

        # project file: sentry_dsn: https://key@sentry.example/1   (log level left out)
        hand_to_the_framework(project_settings, framework_settings)
            ->  framework_settings.sentry_dsn == "https://key@sentry.example/1"
                framework_settings.sincpro_framework_log_level unchanged

    1. The framework's fields the project set — each whose source is not a default.
    2. Each written into `framework`.
    Final: the log configured again when one of them is a log setting.
    """
    handed = [
        name
        for name in FrameworkSettings.model_fields
        if not built._sources.get(name, "default").startswith("default")
    ]
    for name in handed:
        setattr(framework, name, getattr(built, name))
    if any(name.startswith("sincpro_framework_log") for name in handed):
        sincpro_log.configure_global_logging(
            framework.sincpro_framework_log_level,
            backend=framework.sincpro_framework_log_backend,
            file_path=framework.sincpro_framework_log_file_path,
        )
