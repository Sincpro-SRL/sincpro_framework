"""The OTLP exporters of the framework's own pipelines, built from its settings.

Context: `otlp_endpoint`, `otlp_headers` and `otlp_protocol` come from the project's file or from
`OTEL_EXPORTER_OTLP_*`, so a host that cannot set environment variables (Odoo.sh) still reaches a
collector that asks for a token. A setting left unset changes nothing: the exporter keeps reading
its own environment variable, and gRPC stays the default with HTTP as its fallback.
"""

from typing import Any
from urllib.parse import unquote, urlsplit

from sincpro_framework.sincpro_conf import settings

HTTP = "http/protobuf"
METRICS_PATH = "/v1/metrics"
TRACES_PATH = "/v1/traces"


def parse_headers(raw: str | None) -> dict[str, str]:
    """1. `key=value,key2=value2` as OpenTelemetry writes it: values percent-decoded.
    2. A pair without `=` or with an empty key is dropped.
    Final: `{}` when there is nothing, so the caller leaves the exporter's own reading alone.
    """
    headers: dict[str, str] = {}
    for pair in (raw or "").split(","):
        key, separator, value = pair.partition("=")
        if separator and key.strip():
            headers[key.strip()] = unquote(value.strip())
    return headers


def configured_headers() -> dict[str, str]:
    secret = settings.otlp_headers
    return parse_headers(secret.get_secret_value() if secret else None)


def _http_endpoint(endpoint: str, path: str) -> str:
    """The HTTP exporter takes an explicit endpoint as it is — without the signal's path the
    collector answers 404 — so a bare `http://host:4318` gets `/v1/metrics` or `/v1/traces`.
    """
    if urlsplit(endpoint).path in ("", "/"):
        return endpoint.rstrip("/") + path
    return endpoint


def _kwargs(endpoint: str) -> dict[str, Any]:
    headers = configured_headers()
    return {"endpoint": endpoint, **({"headers": headers} if headers else {})}


def span_exporter(endpoint: str) -> Any:
    protocol = settings.otlp_protocol
    if protocol == HTTP:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(**_kwargs(_http_endpoint(endpoint, TRACES_PATH)))
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        if protocol:
            raise
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    return OTLPSpanExporter(**_kwargs(endpoint))


def metric_exporter(endpoint: str) -> Any:
    protocol = settings.otlp_protocol
    if protocol == HTTP:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        return OTLPMetricExporter(**_kwargs(_http_endpoint(endpoint, METRICS_PATH)))
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    except ImportError:
        if protocol:
            raise
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    return OTLPMetricExporter(**_kwargs(endpoint))
