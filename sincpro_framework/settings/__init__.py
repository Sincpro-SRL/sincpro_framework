"""Settings: one document, any shape, one resolution.

    class SharedSettings(SincproConfig):              # what every context shares
        environment: Environment = Environment.TEST

    class QRSettings(SharedSettings):                 # a context: shared + its own
        linkser_endpoint: str = ""

    class PaymentsSettings(SharedSettings):           # the global: shared + every context
        qr: QRSettings

    settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
    qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")

`domain/` holds what a shape is (`SincproConfig`, `Secret`, `FrameworkSettings`) and what a
description is; `adapters/` where the document comes from (the YAML file, the environment by
path, AWS); `services/` how a shape is resolved (sections, nested shapes, the cascade), built and
described; `entrypoint/` `build_config_obj` and the framework's own `settings`, whose door is
`sincpro_framework.sincpro_conf`. See `docs/core/settings.md`.
"""

from sincpro_framework.settings.adapters.yaml_file import load_yaml_file
from sincpro_framework.settings.domain.config import (
    FrameworkSettings,
    Secret,
    SettingSource,
    SincproConfig,
    TypeSincproConfigModel,
)
from sincpro_framework.settings.domain.description import SettingDescription
from sincpro_framework.settings.entrypoint.building import build_config_obj
from sincpro_framework.settings.services.description import describe_settings

__all__ = [
    "FrameworkSettings",
    "Secret",
    "SettingDescription",
    "SettingSource",
    "SincproConfig",
    "TypeSincproConfigModel",
    "build_config_obj",
    "describe_settings",
    "load_yaml_file",
]
