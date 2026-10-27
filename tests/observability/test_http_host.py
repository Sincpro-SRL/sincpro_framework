"""The HTTP host: one line and one span per request — what never reaches a use case included —
never its query string, at the level its status says; and the libraries' logging in the same
format, on the same trace."""

import asyncio
import logging
from typing import Any

import pytest

from sincpro_framework.observability import http
from sincpro_framework.observability.tracing.setup import current_otel_context


class SpyLog:
    """Each line written, with its level, its fields and the trace ids active as it was."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def _line(self, level: str, said: str, **fields: Any) -> None:
        self.lines.append({"level": level, "said": said, **fields, **current_otel_context()})

    def info(self, said: str, **fields: Any) -> None:
        self._line("info", said, **fields)

    def warning(self, said: str, **fields: Any) -> None:
        self._line("warning", said, **fields)

    def error(self, said: str, **fields: Any) -> None:
        self._line("error", said, **fields)


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    spy = SpyLog()
    monkeypatch.setattr(http, "access_log", spy)
    return spy.lines


def answering(status: int):
    """An ASGI app that answers `status` by itself — no use case behind it."""

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


async def failing(scope, receive, send):
    raise RuntimeError("the app broke before answering")


def call(app, path: str = "/", query: bytes = b"", headers: list | None = None) -> list[dict]:
    """One HTTP request through `app`; the messages it sent."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_a_401_no_use_case_saw_leaves_a_line_with_its_trace_and_no_query(lines, otel_setup):
    app = http.http_middleware(answering(401))

    call(app, "/mcp", query=b"mcp_access_token=s3cr3t")

    [line] = lines
    assert (line["level"], line["http_status"], line["http_path"]) == ("warning", 401, "/mcp")
    assert "s3cr3t" not in str(line) and len(line["trace_id"]) == 32


def test_a_413_is_a_warning_and_a_500_an_error():
    seen: list[dict[str, Any]] = []
    spy = SpyLog()
    spy.lines = seen
    original = http.access_log
    http.access_log = spy  # type: ignore[assignment]
    try:
        call(http.http_middleware(answering(413)), "/uploads/1")
        with pytest.raises(RuntimeError):
            call(http.http_middleware(failing), "/boom")
    finally:
        http.access_log = original

    assert [(one["level"], one["http_status"]) for one in seen] == [
        ("warning", 413),
        ("error", 500),
    ]


def test_a_probe_path_is_written_only_when_it_fails(lines):
    call(http.http_middleware(answering(200)), "/health")
    call(http.http_middleware(answering(503)), "/health")

    assert [one["http_status"] for one in lines] == [503]


def test_the_request_runs_in_a_server_span_child_of_its_traceparent(lines, otel_setup):
    incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    call(http.http_middleware(answering(200)), headers=[(b"traceparent", incoming.encode())])
    call(
        http.http_middleware(answering(200), trust_incoming_trace=False),
        headers=[(b"traceparent", incoming.encode())],
    )

    trusted, rooted = otel_setup.get_finished_spans()
    assert format(trusted.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert format(rooted.context.trace_id, "032x") != "4bf92f3577b34da6a3ce929d0e0e4736"
    assert trusted.attributes["http.response.status_code"] == 200


def test_anything_but_http_passes_untouched(lines):
    seen: list[str] = []

    async def lifespan(scope, receive, send):
        seen.append(scope["type"])

    async def receive():
        return {}

    async def send(message):
        return None

    asyncio.run(http.http_middleware(lifespan)({"type": "lifespan"}, receive, send))

    assert seen == ["lifespan"] and lines == []


@pytest.fixture
def root_logging():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    library = logging.getLogger("uvicorn.error")
    library_saved = (list(library.handlers), library.propagate)
    yield root
    for handler in root.handlers:
        if handler not in saved[0]:
            handler.close()
    root.handlers, root.level = saved[0], saved[1]
    library.handlers, library.propagate = library_saved


def test_a_library_logs_through_the_root_in_the_framework_format(root_logging):
    library = logging.getLogger("uvicorn.error")
    library.addHandler(logging.NullHandler())
    library.propagate = False

    http.configure_process_logging("INFO")

    assert library.handlers == [] and library.propagate is True
    [handler] = root_logging.handlers
    assert handler.formatter.__class__.__name__ == "ProcessorFormatter"
    assert root_logging.level == logging.INFO


def test_with_the_host_logging_the_root_is_left_to_the_host(root_logging, monkeypatch):
    monkeypatch.setattr(http.settings, "sincpro_framework_log_backend", "stdlib")
    hosts = logging.Handler()
    root_logging.handlers = [hosts]

    http.configure_process_logging("INFO")

    assert root_logging.handlers == [hosts]
    assert logging.getLogger("uvicorn.error").propagate is True


def test_with_a_log_file_the_libraries_write_to_it(root_logging, monkeypatch, tmp_path):
    written = tmp_path / "service.log"
    monkeypatch.setattr(http.settings, "sincpro_framework_log_backend", "file")
    monkeypatch.setattr(http.settings, "sincpro_framework_log_file_path", str(written))

    http.configure_process_logging("INFO")
    logging.getLogger("uvicorn.error").warning("port in use")
    for handler in root_logging.handlers:
        handler.flush()

    assert "port in use" in written.read_text()
