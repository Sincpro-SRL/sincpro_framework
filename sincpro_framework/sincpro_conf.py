"""Module to handle configuration based on yaml or init"""

import logging
import os
from typing import Annotated, Literal, Type, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

DEFAULT_CONFIG_FILE_PATH = (
    os.getenv("SINCPRO_FRAMEWORK_CONFIG_FILE", default=None)
    or os.path.dirname(__file__) + "/conf/sincpro_framework_conf.yml"
)

logger = logging.getLogger("sincpro_framework")


def load_yaml_file(file_path: str) -> dict:
    """Load a yaml file and return the content as a dictionary"""
    with open(file_path) as file:
        return yaml.safe_load(file)


def usable_env_value(value: str, field_info) -> bool:
    """Whether an env string satisfies the field it is going into.

    ``settings`` is built at import time, so a malformed env var would otherwise
    raise ValidationError before the framework is even importable — a typo in one
    deployment variable would take down the whole process.
    """
    try:
        annotation = field_info.annotation
        if field_info.metadata:
            annotation = Annotated[tuple([annotation, *field_info.metadata])]
        TypeAdapter(annotation).validate_python(value)
        return True
    except Exception:
        return False


class SincproConfig(BaseModel):
    """Base config model"""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=True)

    @model_validator(mode="before")
    def resolve_env_variables(cls, values):
        """Load all environment variables that start with $ENV:
        If the environment variable is not set, an info log is emitted and the default value is used
        """
        for field_name, value in values.items():
            if isinstance(value, str) and value.startswith("$ENV:"):
                env_var_name = value.split("$ENV:")[1]
                env_value = os.getenv(env_var_name)

                field_info = cls.model_fields.get(field_name, None)

                if env_value is not None:
                    if field_info is None or usable_env_value(env_value, field_info):
                        values[field_name] = env_value
                    else:
                        logger.info(
                            f"Environment variable [{env_var_name}] holds [{env_value!r}], "
                            f"which field [{field_name}] cannot accept. "
                            f"Using default value: {field_info.default}"
                        )
                        values[field_name] = field_info.default
                else:
                    if field_info and field_info.default is not None:
                        default_value = field_info.default
                        logger.info(
                            f"Environment variable [{env_var_name}] is not set for field [{field_name}]. "
                            f"Using default value: {default_value}"
                        )
                        values[field_name] = default_value
                    elif field_info is not None and field_info.default is None:
                        # Optional field (default=None) — env var is optional, use None silently
                        values[field_name] = None
                    else:
                        logger.info(
                            f"Environment variable [{env_var_name}] is not set for field [{field_name}] "
                            f"and no default value was provided. This might cause issues."
                        )
        return values


TypeSincproConfigModel = TypeVar("TypeSincproConfigModel", bound=SincproConfig)


class DefaultFrameworkConfig(SincproConfig):
    """Default configuration for the framework"""

    sincpro_framework_log_level: Literal["INFO", "DEBUG"] = "DEBUG"
    sincpro_framework_log_backend: Literal["print", "stdlib", "file"] = "print"
    sincpro_framework_log_file_path: str | None = None
    otlp_endpoint: str | None = None
    otlp_traces_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    sentry_dsn: str | None = None
    app_release: str | None = None
    otel_service_name: str | None = None
    tenant: str | None = None


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
