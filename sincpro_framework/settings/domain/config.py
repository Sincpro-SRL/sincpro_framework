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
from typing import Annotated, ClassVar, Literal, TypeVar, Union, get_args, get_origin

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

from sincpro_framework.remote_execution import HostedContext

logger = logging.getLogger("sincpro_framework")

SENTINEL = re.compile(r"^\$([A-Z][A-Z0-9_]*):(.+)$")
"""`$ENV:NAME`, `$SSM:/path`, `$SECRET:name#key` — a value read from a source, by its name."""

ENV_SENTINEL = "$ENV:"
"""The sentinel of the environment — the one source every build reads without being handed it."""

type SettingSource = Callable[[str], str | None]
"""A name to its value — `None` when the source holds none: a parameter store, a vault."""


class SincproConfig(BaseModel):
    """Base config model"""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=True)

    env_prefix: ClassVar[str] = ""
    """Set on a root shape — `"PAYMENTS"` — and `PAYMENTS__QR__TIMEOUT` sets `qr.timeout`. Empty:
    the environment is read only through `$ENV:` sentinels, as it always was."""

    _sources: dict[str, str] = PrivateAttr(default_factory=dict)
    """Where each value came from, by path — stored on the object `build_config_obj` answers, for
    `describe_settings`."""

    @classmethod
    def field_accepts(cls, name: str, value: str) -> bool:
        """Whether the field `name` of this shape accepts `value` — a string read from the
        environment or a source — its type and its constraints both checked.

        Context: the framework's settings are built at import time, so a malformed variable
        would otherwise raise before the framework is even importable — a typo in one
        deployment variable would take down the whole process. The value is only tried, never
        stored — the model reads it again as it builds.

            # class C(SincproConfig): timeout: int = 30
            #                         rate: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
            C.field_accepts("timeout", "5")      ->  True
            C.field_accepts("timeout", "abc")    ->  False
            C.field_accepts("rate", "1.5")       ->  False    # the constraints are checked too

        1. The field's annotation, with its constraints (`Field(ge=…)`) when it declares any.
        Final: `True` when that annotation validates `value`, else `False`.
        """
        field_info = cls.model_fields[name]
        annotation = field_info.annotation
        if field_info.metadata:
            annotation = Annotated[tuple([annotation, *field_info.metadata])]
        try:
            TypeAdapter(annotation).validate_python(value)
            return True
        except Exception:
            return False

    @classmethod
    def field_holds_secret(cls, name: str) -> bool:
        """Whether the field `name` of this shape holds a `Secret` — `Secret[str]`, or one that
        may be `None`.

            # api_token: Secret[str] · dsn: Secret[str] | None · key: Optional[Secret[int]]
            # masked: SecretStr · name: str
            C.field_holds_secret("api_token")   ->  True
            C.field_holds_secret("dsn")         ->  True
            C.field_holds_secret("key")         ->  True
            C.field_holds_secret("masked")      ->  False   # pydantic's own, not `Secret`
            C.field_holds_secret("name")        ->  False

        1. The members of the annotation: those of a union, else the annotation alone.
        Final: `True` when any member is `Secret`, or a subclass of it, parametrised or not.
        """
        annotation = cls.model_fields[name].annotation
        members = (
            get_args(annotation)
            if get_origin(annotation) in (Union, UnionType)
            else (annotation,)
        )
        for one in members:
            candidate = get_origin(one) or one
            if isinstance(candidate, type) and issubclass(candidate, Secret):
                return True
        return False

    @classmethod
    def field_shape(cls, name: str) -> "type[SincproConfig] | None":
        """The shape the field `name` of this shape holds — a section of its own — or `None` for
        a value.

            # qr: QRSettings · card: CardSettings | None · either: QRSettings | CardSettings
            # many: list[QRSettings] · timeout: int
            C.field_shape("qr")        ->  QRSettings
            C.field_shape("card")      ->  CardSettings
            C.field_shape("either")    ->  None    # which one is not said
            C.field_shape("many")      ->  None    # a list is a value
            C.field_shape("timeout")   ->  None

        1. The members of the annotation: those of a union, else the annotation alone.
        Final: the one member that is a `SincproConfig` — `None` when none is, or two or more.
        """
        annotation = cls.model_fields[name].annotation
        members = (
            get_args(annotation)
            if get_origin(annotation) in (Union, UnionType)
            else (annotation,)
        )
        shapes = [
            one for one in members if isinstance(one, type) and issubclass(one, SincproConfig)
        ]
        return shapes[0] if len(shapes) == 1 else None

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
           3.2 a source handed reads `name` — one that raises leaves the sentinel as written,
               with a warning;
           3.3 a source not handed leaves the sentinel as written, with a warning.
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
                        f"({type(error).__name__}: {error}) — it leaves {value!r} as written"
                    )
                    held = value
            else:
                logger.warning(
                    f"[{field_name}] reads ${source_name}:, a source this build was not "
                    f"handed — it leaves {value!r} as written; pass "
                    f"sources={{'{source_name}': ...}} to build_config_obj to read it"
                )
                held = value
            field_info = cls.model_fields.get(field_name, None)
            if held is not None:
                if field_info is None or cls.field_accepts(field_name, held):
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


class FrameworkSettings(SincproConfig):
    """The framework's own settings — the shape of `sincpro_framework.sincpro_conf.settings`,
    built at import from the framework's file and the environment.

    A project's shared shape may inherit it: the framework then reads what the project set (log
    level and backend, OTLP, Sentry, release) from the project's object instead of its own
    environment, and no `config.py` copies them by hand.

        class SharedSettings(FrameworkSettings):
            environment: Environment = Environment.TEST
    """

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
    The framework's own name for `OTEL_METRICS_EXPORTER`: set to anything but `auto`, it wins."""
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
    """`$ENV:OTEL_METRICS_EXPORTER` — where the metrics go: `otlp`, `prometheus`, `none`
    (`docs/observability/metrics.md`)."""
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
