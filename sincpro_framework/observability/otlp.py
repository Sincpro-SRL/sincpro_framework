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
    """The OTLP headers setting as a dict — `OTEL_EXPORTER_OTLP_HEADERS`'s format.

        parse_headers("authorization=Bearer%20t,x-org=1")
            ->  {"authorization": "Bearer t", "x-org": "1"}

    1. `key=value,key2=value2` as OpenTelemetry writes it: values percent-decoded.
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
    """The headers of `otlp_headers` — a secret, read only here — parsed; `{}` when unset.

    otlp_headers="Authorization=Bearer%20abc"   ->  {"Authorization": "Bearer abc"}
    """
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
    """What an exporter is built with: the endpoint, and the headers only when some are set —
    so an exporter with none keeps reading `OTEL_EXPORTER_OTLP_HEADERS` itself."""
    headers = configured_headers()
    return {"endpoint": endpoint, **({"headers": headers} if headers else {})}


def span_exporter(endpoint: str) -> Any:
    """The OTLP span exporter for `endpoint`, in the configured protocol.

        span_exporter("http://collector:4318")     # otlp_protocol="http/protobuf"
            ->  HTTP OTLPSpanExporter(endpoint="http://collector:4318/v1/traces")
        span_exporter("http://collector:4317")     # no protocol, gRPC installed
            ->  gRPC OTLPSpanExporter(endpoint="http://collector:4317")

    1. `http/protobuf`: the HTTP exporter, the endpoint completed with `/v1/traces`.
    2. Otherwise gRPC.
    2.1 Its package missing and no protocol configured: the HTTP exporter instead, on the
        endpoint as given. A protocol configured explicitly raises instead.
    Final: the exporter, with the configured headers.
    """
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
    """The OTLP metric exporter — the same choice as `span_exporter`, on `/v1/metrics`.

        metric_exporter("http://collector:4318/")   # otlp_protocol="http/protobuf"
            ->  HTTP OTLPMetricExporter(endpoint="http://collector:4318/v1/metrics")
        metric_exporter("http://collector:4317")    # no protocol, gRPC installed
            ->  gRPC OTLPMetricExporter(endpoint="http://collector:4317")

    gRPC missing with no protocol configured: the HTTP exporter on the endpoint as given;
    with one configured, the `ImportError` is raised. The configured headers go with it.
    """
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
