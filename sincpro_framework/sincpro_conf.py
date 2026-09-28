"""Module to handle configuration based on yaml or init

The entry point every project imports — `SincproConfig`, `build_config_obj`, the framework's own
`settings` — as it always has. The implementation lives in `sincpro_framework.settings`: one
document, any shape, one resolution (see `docs/core/settings.md`).
"""

import logging
import os

from sincpro_framework.settings import (
    DefaultFrameworkConfig,
    FrameworkSettings,
    Secret,
    SettingDescription,
    SincproConfig,
    TypeSincproConfigModel,
    build_config_obj,
    describe_settings,
    load_yaml_file,
    usable_env_value,
)

DEFAULT_CONFIG_FILE_PATH = (
    os.getenv("SINCPRO_FRAMEWORK_CONFIG_FILE", default=None)
    or os.path.dirname(__file__) + "/conf/sincpro_framework_conf.yml"
)

logger = logging.getLogger("sincpro_framework")

settings = build_config_obj(DefaultFrameworkConfig, DEFAULT_CONFIG_FILE_PATH)

__all__ = [
    "DEFAULT_CONFIG_FILE_PATH",
    "DefaultFrameworkConfig",
    "FrameworkSettings",
    "Secret",
    "SettingDescription",
    "SincproConfig",
    "TypeSincproConfigModel",
    "build_config_obj",
    "describe_settings",
    "load_yaml_file",
    "logger",
    "settings",
    "usable_env_value",
]
