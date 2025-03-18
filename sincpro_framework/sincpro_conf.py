"""Module to handle configuration based on yaml or init"""

import os
import warnings
from typing import Literal, Type, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_CONFIG_FILE_PATH = (
    os.getenv("SINCPRO_FRAMEWORK_CONFIG_FILE", default=None)
    or os.path.dirname(__file__) + "/conf/sincpro_framework_conf.yml"
)


def load_yaml_file(file_path: str) -> dict:
    """Load a yaml file and return the content as a dictionary"""
    with open(file_path) as file:
        return yaml.safe_load(file)


class SincproConfig(BaseModel):
    """Base config model"""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=True)

    @model_validator(mode="before")
    def resolve_env_variables(cls, values):
        """Load all environment variables that start with $ENV:
        If the environment variable is not set, a warning is emitted and the default value is used
        """
        for field_name, value in values.items():
            if isinstance(value, str) and value.startswith("$ENV:"):
                env_var_name = value.split("$ENV:")[1]
                env_value = os.getenv(env_var_name)

                if env_value is not None:
                    values[field_name] = env_value
                else:
                    # Get default value if available in model field definition
                    field_info = cls.model_fields.get(field_name, None)
                    if field_info and field_info.default is not None:
                        default_value = field_info.default
                        warnings.warn(
                            f"Environment variable [{env_var_name}] is not set for field [{field_name}]. "
                            f"Using default value: {default_value}"
                        )
                        values[field_name] = default_value
                    else:
                        warnings.warn(
                            f"Environment variable [{env_var_name}] is not set for field [{field_name}] "
                            f"and no default value was provided. This might cause issues."
                        )
        return values


TypeSincproConfigModel = TypeVar("TypeSincproConfigModel", bound=SincproConfig)


class DefaultFrameworkConfig(SincproConfig):
    """Default configuration for the framework"""

    sincpro_framework_log_level: Literal["INFO", "DEBUG"] = "DEBUG"


def build_config_obj(
    class_config_obj: Type[TypeSincproConfigModel],
    config_path: str,
    sub_key: str | None = None,
) -> TypeSincproConfigModel:
    """Build a config object from a dictionary
    if sub_key is provided, it will return the sub_key of the config
    """
    config_dict = load_yaml_file(config_path)

    if sub_key:
        config_section = config_dict.get(sub_key, None)
        if config_section is None:
            raise ValueError(f"Config section {sub_key} not found in {config_path}")
        config_dict = config_dict[sub_key]

    print(f"read yaml file {config_path} for config {class_config_obj.__name__}")
    return class_config_obj(**config_dict)


settings = build_config_obj(DefaultFrameworkConfig, DEFAULT_CONFIG_FILE_PATH)
