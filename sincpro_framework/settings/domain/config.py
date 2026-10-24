"""What a settings class is: `SincproConfig` and the framework's own shapes.

    class QRSettings(SharedSettings):
        linkser_endpoint: str = "https://api.linkser.com"
        api_token: Secret[str]

Context: a shape is any `SincproConfig` class. A field whose value is `$ENV:NAME` reads that
variable; one it cannot accept falls back to the field's default with a log line, so a typo in
one deployment variable never takes the process down at import. `env_prefix` opts a root shape
into the environment by path (`PAYMENTS__QR__TIMEOUT`). Nothing is frozen unless a shape asks.

Another source is read the same way, by its own sentinel, once the project hands it over:

    token: $SSM:/billing/prod/token                      # in the file
    build_config_obj(Billing, "conf/billing.yml", "billing", sources={"SSM": ParameterStore()})

A source is any function from a name to its value, `None` when it holds none — the same rules
apply to what it answers as to the environment's. A source the build was not handed, or one that
fails (no client, no permission, no network), never stops the start: the value stays as written,
with a warning — and falls back to the default when the field cannot take it.
"""

import logging
import os
import re
from collections.abc import Callable
from types import UnionType
from typing import Annotated, Any, ClassVar, Literal, TypeVar, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    Secret,
    TypeAdapter,
    ValidationInfo,
    model_validator,
)

from sincpro_framework.common.transport.addresses import HostedContext

logger = logging.getLogger("sincpro_framework")

SENTINEL = re.compile(r"^\$([A-Z][A-Z0-9_]*):(.+)$")
"""`$ENV:NAME`, `$SSM:/path`, `$SECRET:name#key` — a value read from a source, by its name."""

type SettingSource = Callable[[str], str | None]
"""A name to its value — `None` when the source holds none: a parameter store, a vault."""


def usable_env_value(value: str, field_info) -> bool:
    """Whether an env string satisfies the field it is going into.

    ``settings`` is built at import time, so a malformed env var would otherwise
    raise ValidationError before the framework is even importable — a typo in one
    deployment variable would take down the whole process.

        usable_env_value("5", <timeout: int>)        ->  True
        usable_env_value("abc", <timeout: int>)      ->  False
        usable_env_value("1.5", <rate: Annotated[float, Field(ge=0.0, le=1.0)]>)
            ->  False    # the field's constraints are checked too

    Context: the value is only tried, never kept — the model reads it again as it builds.
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
    def resolve_env_variables(cls, values, info: ValidationInfo):
        """Read every value written as a sentinel: `$ENV:NAME` from the environment, any other
        (`$SSM:/path`) from the source the build was handed under that name. A value the source
        does not hold, or the field cannot accept, falls back to the default with a log line.

            # class C(SincproConfig): timeout: int = 30; name: str | None = "x"
            # environment: TIMEOUT=5, BAD=abc; NOT_SET unset
            C(timeout="$ENV:TIMEOUT")              ->  C(timeout=5, …)
            C(timeout="$ENV:BAD")                  ->  C(timeout=30, …)    # logged, default
            C(name="$ENV:NOT_SET")                 ->  C(name="x", …)      # logged, default
            C(name="$SSM:/billing/name")           # no "SSM" source handed
                ->  C(name="$SSM:/billing/name", …), with a warning naming `sources=`

        1. Anything but a dict of values is handed on as it is.
        2. The sources the build was handed, from the validation context.
        3. Each value that is a sentinel — `$<SOURCE>:<name>` — any other left alone:
           3.1 `ENV` reads the environment;
           3.2 a source handed reads `name` — one that raises keeps the sentinel as written,
               with a warning;
           3.3 a source not handed keeps the sentinel as written, with a warning.
        4. What was read:
           4.1 held, and the field takes it (or no field declares the key): the value;
           4.2 held, and the field refuses it: the field's default, logged;
           4.3 not held, a field with a default other than `None`: that default, logged;
           4.4 not held, a field defaulting to `None`: `None`, silently;
           4.5 not held, no such field: left as written, logged.
        Final: the values, sentinels replaced, for the model to validate.
        """
        if not isinstance(values, dict):
            return values
        given: dict[str, SettingSource] = (info.context or {}).get("sources", {})
        for field_name, value in values.items():
            found = SENTINEL.match(value) if isinstance(value, str) else None
            if found is None:
                continue
            source_name, name = found.groups()
            said = (
                f"Environment variable [{name}]"
                if source_name == "ENV"
                else f"[${source_name}:{name}]"
            )
            if source_name == "ENV":
                held = os.getenv(name)
            elif source_name in given:
                try:
                    held = given[source_name](name)
                except Exception as error:  # noqa: BLE001 - settings never stop the start
                    logger.warning(
                        f"[{field_name}] could not be read from ${source_name}: "
                        f"({type(error).__name__}: {error}) — it keeps {value!r} as written"
                    )
                    held = value
            else:
                logger.warning(
                    f"[{field_name}] reads ${source_name}:, a source this build was not "
                    f"handed — it keeps {value!r} as written; pass "
                    f"sources={{'{source_name}': ...}} to build_config_obj to read it"
                )
                held = value
            field_info = cls.model_fields.get(field_name, None)
            if held is not None:
                if field_info is None or usable_env_value(held, field_info):
                    values[field_name] = held
                else:
                    logger.info(
                        f"{said} holds [{held!r}], which field [{field_name}] cannot accept. "
                        f"Using default value: {field_info.default}"
                    )
                    values[field_name] = field_info.default
            elif field_info and field_info.default is not None:
                logger.info(
                    f"{said} is not set for field [{field_name}]. "
                    f"Using default value: {field_info.default}"
                )
                values[field_name] = field_info.default
            elif field_info is not None and field_info.default is None:
                # Optional field (default=None) — the source is optional, use None silently
                values[field_name] = None
            else:
                logger.info(
                    f"{said} is not set for field [{field_name}] "
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
    otlp_headers: Secret[str] | None = None
    """`$ENV:OTEL_EXPORTER_OTLP_HEADERS` — headers the OTLP exporters send, `key=value,key2=value2`
    (a token among them, so masked). Unset: the exporter reads the same variable by itself."""
    otlp_protocol: Literal["grpc", "http/protobuf"] | None = None
    """`$ENV:OTEL_EXPORTER_OTLP_PROTOCOL` — `grpc` or `http/protobuf`. Unset: gRPC, and HTTP when
    the gRPC exporter is not installed."""
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

    context_map: list[HostedContext] = []
    """Where bounded contexts are hosted by another service — `[{context: billing, at: grpc://…}]`,
    each address validated when the file is loaded."""
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
    """Whether a field holds a `Secret` — `Secret[str]`, or one that may be `None`.

        is_secret(Secret[str])            ->  True
        is_secret(Secret[str] | None)     ->  True
        is_secret(Optional[Secret[int]])  ->  True
        is_secret(SecretStr)              ->  False   # pydantic's own, not `Secret`
        is_secret(str)                    ->  False

    Context: a union is a secret when any of its members is; a subclass of `Secret` is one.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(is_secret(one) for one in get_args(annotation))
    candidate = origin or annotation
    return isinstance(candidate, type) and issubclass(candidate, Secret)
