# The framework's own settings

`sincpro_framework.sincpro_conf.settings` is a `DefaultFrameworkConfig` built at import from the
framework's packaged `conf/sincpro_framework_conf.yml`, or from the file named by
`SINCPRO_FRAMEWORK_CONFIG_FILE`. The packaged file maps each field to its environment variable, so a
service usually configures the framework with environment variables alone.

| Field | Variable (packaged file) | Default | What it drives |
|---|---|---|---|
| `app_release` | `APP_RELEASE` | `None` | `artifact:version`; Sentry release and OTel `service.name` |
| `tenant` | `TENANT` | `None` | the `tenant` tag on every signal; GlitchTip environment |
| `sincpro_framework_log_level` | `SINCPRO_FRAMEWORK_LOG_LEVEL` | `DEBUG` | `INFO` or `DEBUG` |
| `sincpro_framework_log_backend` | `SINCPRO_FRAMEWORK_LOG_BACKEND` | `print` | `print`, `stdlib` or `file` |
| `sincpro_framework_log_file_path` | `SINCPRO_FRAMEWORK_LOG_FILE_PATH` | `None` | the file for the `file` backend |
| `sentry_dsn` | `SENTRY_PYTHON_DSN` | `None` | error reporting; needs the `sentry` extra |
| `otlp_endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | `None` | trace export; needs the `opentelemetry` extra |
| `otlp_traces_sample_rate` | `OTEL_TRACES_SAMPLER_ARG` | `1.0` | share of new traces recorded, `0.0`–`1.0` |
| `otel_service_name` | `OTEL_SERVICE_NAME` | `None` | names the service only when `APP_RELEASE` is absent; ignored when `APP_RELEASE` is set, even to a bare version — set `APP_RELEASE=artifact:version` |
| `otel_metrics_exporter` | `OTEL_METRICS_EXPORTER` | `None` | `otlp`, `prometheus`, `none` |
| `otel_traces_exporter` | `OTEL_TRACES_EXPORTER` | `None` | `otlp` or `none` |
| `otel_sdk_disabled` | `OTEL_SDK_DISABLED` | `False` | `true` turns every OpenTelemetry signal of the framework off |
| `metrics_backend` | `SINCPRO_METRICS_BACKEND` | `auto` | `auto`, `prometheus`, `otel`, `off`; anything but `auto` wins over `OTEL_METRICS_EXPORTER` |
| `metric_labels` | (file only) | `[]` | context keys on every metric series of the process |
| `context_map` | (file only) | `[]` | `[{context: billing, at: grpc://…}]`: contexts hosted by another service |
| `context_map_override` | `SINCPRO_CONTEXT_MAP` | `None` | `billing=grpc://host:port?timeout=5,…`, winning per context |

## Setting them from the project's own document

```python
from sincpro_framework.sincpro_conf import FrameworkSettings


class SharedSettings(FrameworkSettings):
    environment: Environment = Environment.TEST
```

```yaml
sincpro_payments_sdk:
  app_release: $ENV:APP_RELEASE
  sentry_dsn: $ENV:SENTRY_PYTHON_DSN
  sincpro_framework_log_level: INFO
```

- Building the project's shape copies into the framework's `settings` every one of those fields the
  project **set**: from the file, the environment, or inherited. A field at its default, or a
  `$ENV:` variable nobody set, is not copied: the framework's own file and environment still decide
  it.
- When a log field is among those copied, the log is configured again.
- A project that does not inherit `FrameworkSettings` changes nothing of the framework's settings.
- Order matters: the context map is read when a `UseFramework` is created, the rest when a bus is
  built. Build the project's singleton before any bus exists.
- The framework's `settings` object stays assignable (`settings.sincpro_framework_log_level = ...`
  works); assign before the buses are created and built, for the reason above.
