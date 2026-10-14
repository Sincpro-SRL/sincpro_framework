"""`otlp_headers` and `otlp_protocol`: what a host without environment variables (Odoo.sh) sets
in its file reaches the OTLP exporters, and unset they change nothing."""

import pytest
from pydantic import Secret

from sincpro_framework.observability import otlp
from sincpro_framework.sincpro_conf import settings


def fake_module(monkeypatch, path: str, name: str) -> dict:
    seen: dict = {}

    def fake_exporter(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(f"opentelemetry.exporter.otlp.proto.{path}.{name}", fake_exporter)
    return seen


@pytest.fixture
def conf(monkeypatch):
    monkeypatch.setattr(settings, "otlp_headers", None)
    monkeypatch.setattr(settings, "otlp_protocol", None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, {}),
        ("", {}),
        ("Authorization=Bearer abc", {"Authorization": "Bearer abc"}),
        ("a=1, b = 2", {"a": "1", "b": "2"}),
        ("Authorization=Bearer%20abc", {"Authorization": "Bearer abc"}),
        ("token=a=b", {"token": "a=b"}),
        ("no-equals,=empty-key,ok=1", {"ok": "1"}),
    ],
)
def test_headers_are_read_as_opentelemetry_writes_them(raw, expected):
    assert otlp.parse_headers(raw) == expected


def test_headers_are_a_secret_so_describe_does_not_show_the_token():
    field = type(settings).model_fields["otlp_headers"]
    assert "Secret" in str(field.annotation)
    assert Secret("x").get_secret_value() == "x"


def test_headers_reach_the_span_exporter(monkeypatch, conf):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    seen = fake_module(monkeypatch, "grpc.trace_exporter", "OTLPSpanExporter")
    monkeypatch.setattr(settings, "otlp_headers", Secret("Authorization=Bearer abc"))

    otlp.span_exporter("http://alloy:4317")

    assert seen == {
        "endpoint": "http://alloy:4317",
        "headers": {"Authorization": "Bearer abc"},
    }


def test_headers_reach_the_metric_exporter(monkeypatch, conf):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    seen = fake_module(monkeypatch, "grpc.metric_exporter", "OTLPMetricExporter")
    monkeypatch.setattr(settings, "otlp_headers", Secret("Authorization=Bearer abc"))

    otlp.metric_exporter("http://alloy:4317")

    assert seen["headers"] == {"Authorization": "Bearer abc"}


def test_with_no_headers_the_exporter_is_left_to_read_its_own_environment(monkeypatch, conf):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    seen = fake_module(monkeypatch, "grpc.trace_exporter", "OTLPSpanExporter")

    otlp.span_exporter("http://alloy:4317")

    assert seen == {"endpoint": "http://alloy:4317"}


def test_http_protocol_builds_the_http_exporter_with_the_signal_path(monkeypatch, conf):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    metrics = fake_module(monkeypatch, "http.metric_exporter", "OTLPMetricExporter")
    traces = fake_module(monkeypatch, "http.trace_exporter", "OTLPSpanExporter")
    monkeypatch.setattr(settings, "otlp_protocol", "http/protobuf")

    otlp.metric_exporter("http://alloy:4318")
    otlp.span_exporter("http://alloy:4318/")

    assert metrics["endpoint"] == "http://alloy:4318/v1/metrics"
    assert traces["endpoint"] == "http://alloy:4318/v1/traces"


def test_http_protocol_keeps_an_endpoint_that_already_has_a_path(monkeypatch, conf):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    seen = fake_module(monkeypatch, "http.metric_exporter", "OTLPMetricExporter")
    monkeypatch.setattr(settings, "otlp_protocol", "http/protobuf")

    otlp.metric_exporter("https://gateway.example/otlp/v1/metrics")

    assert seen["endpoint"] == "https://gateway.example/otlp/v1/metrics"


def test_an_unknown_protocol_in_the_environment_does_not_take_the_process_down(tmp_path):
    from sincpro_framework.settings import DefaultFrameworkConfig, build_config_obj

    file = tmp_path / "conf.yml"
    file.write_text("otlp_protocol: $ENV:SINCPRO_TEST_PROTOCOL\n")
    import os

    os.environ["SINCPRO_TEST_PROTOCOL"] = "carrier-pigeon"
    try:
        built = build_config_obj(DefaultFrameworkConfig, str(file))
    finally:
        del os.environ["SINCPRO_TEST_PROTOCOL"]

    assert built.otlp_protocol is None


def test_headers_and_protocol_set_in_the_projects_file_reach_the_framework(tmp_path):
    from sincpro_framework.settings import FrameworkSettings, build_config_obj

    class ProjectSettings(FrameworkSettings):
        pass

    file = tmp_path / "conf.yml"
    file.write_text(
        "otlp_headers: Authorization=Bearer abc\n" "otlp_protocol: http/protobuf\n"
    )
    before = (settings.otlp_headers, settings.otlp_protocol)
    try:
        build_config_obj(ProjectSettings, str(file))
        assert settings.otlp_headers is not None
        assert settings.otlp_headers.get_secret_value() == "Authorization=Bearer abc"
        assert settings.otlp_protocol == "http/protobuf"
        assert "abc" not in repr(settings.otlp_headers)
    finally:
        settings.otlp_headers, settings.otlp_protocol = before
