"""The framework's settings, by the door every project has always imported them from.

    from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj, settings
    settings.sentry_dsn          # what the framework reads to report errors

Context: the implementation lives in `sincpro_framework.settings` — one document, any shape, one
resolution (`docs/core/settings.md`) — and both doors answer the same objects, so a project
written against either keeps working.
"""

from sincpro_framework.settings import (
    FrameworkSettings,
    Secret,
    SettingDescription,
    SincproConfig,
    TypeSincproConfigModel,
    build_config_obj,
    describe_settings,
    load_yaml_file,
)
from sincpro_framework.settings.entrypoint.building import DEFAULT_CONFIG_FILE_PATH, settings

__all__ = [
    "DEFAULT_CONFIG_FILE_PATH",
    "FrameworkSettings",
    "Secret",
    "SettingDescription",
    "SincproConfig",
    "TypeSincproConfigModel",
    "build_config_obj",
    "describe_settings",
    "load_yaml_file",
    "settings",
]
