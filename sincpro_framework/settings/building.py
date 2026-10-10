"""`build_config_obj`: a shape resolved against a project's file — the one function there is.

    settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
    qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")

Context: the same call a project has always made. A flat class, read at a section without a dot,
is built exactly as it always was; nested shapes, the cascade, a dotted path and `env_prefix` are
reached only by a project that writes them. A shape inheriting `FrameworkSettings` hands the
framework what it set.
"""

import os
from typing import Type

from sincpro_framework.settings.adapters.environment import with_environment_by_path
from sincpro_framework.settings.adapters.yaml_file import load_yaml_file
from sincpro_framework.settings.domain.config import (
    DefaultFrameworkConfig,
    FrameworkSettings,
    SincproConfig,
    TypeSincproConfigModel,
)
from sincpro_framework.settings.domain.resolution import locate, values_of


def _hand_to_the_framework(built: FrameworkSettings) -> None:
    """What a project's shape set of the framework's own settings, copied into the framework's —
    and the log configured again when a log setting was among them.

    Context: the framework's modules hold its settings object from import time, so the project's
    values reach them by being written into it; a value the project left at its default is not
    written, so the framework's own environment still decides it.
    """
    from sincpro_log import configure_global_logging

    from sincpro_framework.sincpro_conf import settings

    sources = built._sources
    handed = [
        name
        for name in DefaultFrameworkConfig.model_fields
        if not sources.get(name, "default").startswith("default")
    ]
    for name in handed:
        setattr(settings, name, getattr(built, name))
    if any(name.startswith("sincpro_framework_log") for name in handed):
        configure_global_logging(
            settings.sincpro_framework_log_level,
            backend=settings.sincpro_framework_log_backend,
            file_path=settings.sincpro_framework_log_file_path,
        )


def _keep_sources(built: SincproConfig, sources: dict[str, str], prefix: str) -> None:
    """Each section keeps where its own values came from, by its own paths — so a context handed
    `settings.qr` describes it as if it had been built alone."""
    built._sources = {
        path.removeprefix(prefix): source
        for path, source in sources.items()
        if path.startswith(prefix)
    }
    for name in type(built).model_fields:
        value = getattr(built, name)
        if isinstance(value, SincproConfig):
            _keep_sources(value, sources, f"{prefix}{name}.")


def build_config_obj(
    class_config_obj: Type[TypeSincproConfigModel],
    config_path: str,
    sub_key: str | None = None,
) -> TypeSincproConfigModel:
    """Build a config object from a dictionary
    if sub_key is provided, it will return the sub_key of the config — a dotted path reaches a
    section inside another (`"sincpro_payments_sdk.qr"`).

    1. The document: the file, and — for a shape declaring `env_prefix` — the environment by path.
    2. The section `sub_key` names, and the sections above it inside the project's own.
    3. The shape's values: its section, its nested shapes, the cascade (`values_of`).
    4. Final: validated whole in one call — every problem one error, by path — with where each
       value came from kept for `describe_settings`.
    """
    document = load_yaml_file(config_path)
    prefix = class_config_obj.env_prefix
    if prefix:
        anchor = sub_key.split(".")[0] if sub_key else None
        document = with_environment_by_path(document, prefix, anchor, os.environ)
    section, above = locate(document, sub_key, config_path)

    print(f"read yaml file {config_path} for config {class_config_obj.__name__}")
    sources: dict[str, str] = {}
    values = values_of(class_config_obj, section, sub_key or "", config_path, above, sources)
    built = class_config_obj(**values)
    _keep_sources(built, sources, "")
    if isinstance(built, FrameworkSettings):
        _hand_to_the_framework(built)
    return built
