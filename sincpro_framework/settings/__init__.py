"""Settings: one document, any shape, one resolution.

    class SharedSettings(SincproConfig):              # what every context shares
        environment: Environment = Environment.TEST

    class QRSettings(SharedSettings):                 # a context: shared + its own
        linkser_endpoint: str = ""

    class PaymentsSettings(SharedSettings):           # the global: shared + every context
        qr: QRSettings

    settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
    qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")

`domain/` holds what a shape is (`SincproConfig`, `Secret`, the framework's shapes) and how one is
resolved (sections, nested shapes, the cascade); `adapters/` where the document comes from (the
YAML file, the environment by path); `building` the one function; `describe` where each value came
from. `sincpro_framework.sincpro_conf` is the entry point projects import, as always. See
`docs/core/settings.md`.
"""

from sincpro_framework.settings.adapters.yaml_file import load_yaml_file
from sincpro_framework.settings.building import build_config_obj
from sincpro_framework.settings.describe import SettingDescription, describe_settings
from sincpro_framework.settings.domain.config import (
    DefaultFrameworkConfig,
    FrameworkSettings,
    Secret,
    SincproConfig,
    TypeSincproConfigModel,
    usable_env_value,
)

__all__ = [
    "DefaultFrameworkConfig",
    "FrameworkSettings",
    "Secret",
    "SettingDescription",
    "SincproConfig",
    "TypeSincproConfigModel",
    "build_config_obj",
    "describe_settings",
    "load_yaml_file",
    "usable_env_value",
]
