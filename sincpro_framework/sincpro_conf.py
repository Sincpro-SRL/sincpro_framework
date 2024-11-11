"""Module to handle configuration based on yaml or init"""

import os
from typing import Literal, Type, TypeVar

import yaml
from pydantic import BaseModel, model_validator

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

    model_config = {"arbitrary_types_allowed": True, "allow_mutations": False}

    @model_validator(mode="before")
    def resolve_env_variables(cls, values):
        """Load all environment variables that start with $ENV:"""
        for field_name, value in values.items():
            if isinstance(value, str) and value.startswith("$ENV:"):
                env_var_name = value.split("$ENV:")[1]
                env_value = os.getenv(env_var_name)
                if env_value is not None:
                    values[field_name] = env_value
                else:
                    raise ValueError(
                        f"The Environment variable [{env_var_name}] is not set for field [{field_name}]"
                    )
        return values


TypeSincproConfigModel = TypeVar("TypeSincproConfigModel", bound=SincproConfig)


class DefaultFrameworkConfig(SincproConfig):
    """Default configuration for the framework"""

    sincpro_framework_log_level: Literal["INFO", "DEBUG"] = "DEBUG"


def build_config_obj(
    class_config_obj: Type[TypeSincproConfigModel], config_path: str
) -> TypeSincproConfigModel:
    """Build a config object from a dictionary"""
    config_dict = load_yaml_file(config_path)
    return class_config_obj(**config_dict)


settings = build_config_obj(DefaultFrameworkConfig, DEFAULT_CONFIG_FILE_PATH)
