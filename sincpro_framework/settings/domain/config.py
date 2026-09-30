"""What a settings class is: `SincproConfig` and the framework's own shapes.

    class QRSettings(SharedSettings):
        linkser_endpoint: str = "https://api.linkser.com"
        api_token: Secret[str]

Context: a shape is any `SincproConfig` class. A field whose value is `$ENV:NAME` reads that
variable; one it cannot accept falls back to the field's default with a log line, so a typo in
one deployment variable never takes the process down at import. `env_prefix` opts a root shape
into the environment by path (`PAYMENTS__QR__TIMEOUT`). Nothing is frozen unless a shape asks.
"""

import logging
import os
from types import UnionType
from typing import Annotated, Any, ClassVar, Literal, TypeVar, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    Secret,
    TypeAdapter,
    model_validator,
)

logger = logging.getLogger("sincpro_framework")

__all__ = [
    "DefaultFrameworkConfig",
    "FrameworkSettings",
    "Secret",
    "SincproConfig",
    "TypeSincproConfigModel",
    "usable_env_value",
]


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

    env_prefix: ClassVar[str] = ""
    """Set on a root shape — `"PAYMENTS"` — and `PAYMENTS__QR__TIMEOUT` sets `qr.timeout`. Empty:
    the environment is read only through `$ENV:` sentinels, as it always was."""

    _sources: dict[str, str] = PrivateAttr(default_factory=dict)
    """Where each value came from, by path — kept on the object `build_config_obj` answers, for
    `describe_settings`."""

    def __eq__(self, other: object) -> bool:
        """Pydantic's equality, blind to `_sources`: where a value came from is not the value, and
        a built object equals one written by hand with the same values, as it always did."""
        if not isinstance(other, BaseModel):
            return NotImplemented
        mine, theirs = self.__pydantic_private__ or {}, other.__pydantic_private__ or {}
        return (
            type(self) is type(other)
            and self.__dict__ == other.__dict__
            and self.__pydantic_extra__ == other.__pydantic_extra__
            and {k: v for k, v in mine.items() if k != "_sources"}
            == {k: v for k, v in theirs.items() if k != "_sources"}
        )

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

    app_release: str | None = None
    """`$ENV:APP_RELEASE` — The release version of the application."""
    tenant: str | None = None
    """`$ENV:TENANT` — The tenant identifier for the application."""
    sincpro_framework_log_level: Literal["INFO", "DEBUG"] = "DEBUG"
    """`$ENV:SINCPRO_FRAMEWORK_LOG_LEVEL` — The log level for the framework."""

    sincpro_framework_log_backend: Literal["print", "stdlib", "file"] = "print"
    """`$ENV:SINCPRO_FRAMEWORK_LOG_BACKEND` — The log backend for the framework."""
    sincpro_framework_log_file_path: str | None = None
    """`$ENV:SINCPRO_FRAMEWORK_LOG_FILE_PATH` — The file path for the framework's log file."""

    metrics_backend: Literal["auto", "prometheus", "otel", "off"] = "auto"
    """`$ENV:SINCPRO_METRICS_BACKEND` — where the metrics go (`docs/observability/metrics.md`).
    Kept as an alias: set to anything but `auto`, it wins over `OTEL_METRICS_EXPORTER`."""
    sentry_dsn: str | None = None
    """`$ENV:SENTRY_PYTHON_DSN` — Sentry DSN for error tracking."""
    otlp_endpoint: str | None = None
    """`$ENV:OTEL_EXPORTER_OTLP_ENDPOINT` — OpenTelemetry OTLP endpoint for the framework."""
    otlp_traces_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    """`$ENV:OTEL_TRACES_SAMPLER_ARG` — OpenTelemetry traces sample rate for the framework."""
    otel_service_name: str | None = None
    """`$ENV:OTEL_SERVICE_NAME` — OpenTelemetry service name for the framework."""
    otel_metrics_exporter: str | None = None
    """`$ENV:OTEL_METRICS_EXPORTER` — OpenTelemetry's own switch: `otlp`, `prometheus`, `none`."""
    otel_traces_exporter: str | None = None
    """`$ENV:OTEL_TRACES_EXPORTER` — `otlp` or `none`; `none` builds no provider of the framework's."""
    otel_sdk_disabled: bool = False
    """`$ENV:OTEL_SDK_DISABLED` — `true` turns every OpenTelemetry signal of the framework off."""
    metric_labels: list[str] = []
    """Context keys on every metric series of the process, beside release, service, version and
    tenant — `[company, channel]` (PRD_03 §4.10)."""

    context_map: list[dict[str, str]] = []
    """Where bounded contexts are hosted by another service — `[{context: billing, at: grpc://…}]`."""
    context_map_override: str | None = None
    """`$ENV:SINCPRO_CONTEXT_MAP` — `billing=grpc://host:port?timeout=5,…`, winning per context."""


class FrameworkSettings(DefaultFrameworkConfig):
    """The framework's own settings, inherited by a project's shared shape — the framework then
    reads what the project set (log level and backend, OTLP, Sentry, release) from the project's
    object instead of its own environment, and no `config.py` copies them by hand.

        class SharedSettings(FrameworkSettings):
            environment: Environment = Environment.TEST
    """


def is_secret(annotation: Any) -> bool:
    """Whether a field holds a `Secret` — `Secret[str]`, or one that may be `None`."""
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(is_secret(one) for one in get_args(annotation))
    candidate = origin or annotation
    return isinstance(candidate, type) and issubclass(candidate, Secret)
