"""`build_config_obj`: a shape resolved against a project's file — the one function there is —
and `settings`, the framework's own, built with it at import.

    settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
    qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")

Context: the framework's own `settings` lives here, beside the function that hands it what a
project's shape inheriting `FrameworkSettings` set — `sincpro_framework.sincpro_conf` is its door.
The file is the framework's `conf/sincpro_framework_conf.yml`, or the one
`SINCPRO_FRAMEWORK_CONFIG_FILE` names.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from sincpro_framework.settings.domain.config import (
    FrameworkSettings,
    SettingSource,
    TypeSincproConfigModel,
)
from sincpro_framework.settings.services.building import build_settings, hand_to_the_framework

DEFAULT_CONFIG_FILE_PATH = os.getenv("SINCPRO_FRAMEWORK_CONFIG_FILE", default=None) or str(
    Path(__file__).parents[2] / "conf" / "sincpro_framework_conf.yml"
)


def build_config_obj(
    class_config_obj: type[TypeSincproConfigModel],
    config_path: str,
    sub_key: str | None = None,
    sources: Mapping[str, SettingSource] | None = None,
) -> TypeSincproConfigModel:
    """Build `class_config_obj` from the YAML file at `config_path`, read at the section
    `sub_key` names — a dotted path reaches a section inside another
    (`"sincpro_payments_sdk.qr"`); no `sub_key` reads the whole document.

        build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")
            ->  QRSettings(linkser_endpoint="https://…", environment=Environment.PROD)

    1. The shape built (`build_settings`). A value written `$<SOURCE>:name` is read from
       `sources[SOURCE]` — `$ENV:` needs none.
    2. A `FrameworkSettings` a project builds — the shape itself, or one inheriting it — hands
       the framework's `settings` what it set (`hand_to_the_framework`).
    Final: the built object.
    """
    built = build_settings(class_config_obj, config_path, sub_key, sources)
    if isinstance(built, FrameworkSettings):
        hand_to_the_framework(built, settings)
    return built


settings = build_settings(FrameworkSettings, DEFAULT_CONFIG_FILE_PATH, None, None)
"""The framework's own settings, built once from its file and the environment — what every
`FrameworkSettings` a project builds hands its values to."""
