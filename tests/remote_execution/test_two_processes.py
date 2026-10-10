"""Two services, two processes, one bounded context: the zero-configuration path, end to end.

The context is written to disk and imported by both. Service B hosts it — `billing.serve(...)`
over gRPC, or the HTTP route on an app of its own — and service A has no line of code about B:
only `SINCPRO_CONTEXT_MAP` in its environment. A executes the context's use cases as it always
would, 64 MiB of bytes included, and prints what it got back — and what crossed the wire: the
command, the response and the domain error, each one JSON message and never a pickle.
"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

CONTEXT = """
import os
import time
from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.exceptions import DomainError


class InvoiceRefused(DomainError):
    pass


class CommandIssueInvoice(DataTransferObject):
    total: Decimal
    pdf: bytes


class ResponseIssueInvoice(DataTransferObject):
    total: Decimal
    pdf_size: int
    issued_by: int | None
    pid: int


class CommandRefuse(DataTransferObject):
    reason: str


class CommandSlow(DataTransferObject):
    seconds: float


class CommandEcho(DataTransferObject):
    data: bytes


class ResponseEcho(DataTransferObject):
    data: bytes


billing = UseFramework("billing-two-processes", log_after_execution=False)


@billing.feature(CommandIssueInvoice)
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        return ResponseIssueInvoice(
            total=dto.total,
            pdf_size=len(dto.pdf),
            issued_by=self.context.get("user_id"),
            pid=os.getpid(),
        )


@billing.feature(CommandRefuse)
class Refuse(Feature):
    def execute(self, dto: CommandRefuse) -> None:
        raise InvoiceRefused(dto.reason)


@billing.feature(CommandSlow)
class Slow(Feature):
    def execute(self, dto: CommandSlow) -> None:
        time.sleep(dto.seconds)


@billing.feature(CommandEcho)
class Echo(Feature):
    def execute(self, dto: CommandEcho) -> ResponseEcho:
        return ResponseEcho(data=dto.data)
"""

SERVERS = {
    "grpc": """
import sys

from billing_two_processes import billing

billing.serve(sys.argv[1])
""",
    "http": """
import sys

import uvicorn
from starlette.applications import Starlette

from billing_two_processes import billing
from sincpro_framework.remote_execution import open_host_routes

host, _, port = sys.argv[1].rpartition(":")
app = Starlette(routes=open_host_routes([billing]))
uvicorn.run(app, host=host, port=int(port), log_level="warning")
""",
}

CLIENT = """
import hashlib
import json
import os
from decimal import Decimal

from billing_two_processes import (
    CommandEcho,
    CommandIssueInvoice,
    CommandRefuse,
    CommandSlow,
    InvoiceRefused,
    ResponseEcho,
    ResponseIssueInvoice,
    billing,
)
from sincpro_framework.remote_execution.domain.payload import Payload
from sincpro_framework.remote_execution import ContextTimeout
from sincpro_framework.remote_execution.domain import errors

wire: list[bytes] = []
write, read, read_details = Payload.as_json, Payload.from_json.__func__, errors.read_values


def written(self):
    raw = write(self)
    wire.append(bytes(raw[:64]))
    return raw


def received(cls, raw):
    wire.append(bytes(raw[:64]))
    return read(cls, raw)


def details(raw):
    wire.append(bytes(raw[:64]))
    return read_details(raw)


Payload.as_json = written
Payload.from_json = classmethod(received)
errors.read_values = details

pdf = bytes(range(256)) * 400
with billing.context({"user_id": 7}):
    answer = billing(CommandIssueInvoice(total=Decimal("12.50"), pdf=pdf), ResponseIssueInvoice)

try:
    billing(CommandRefuse(reason="over the credit limit"))
    refused = None
except InvoiceRefused as error:
    refused = str(error)

try:
    billing(CommandSlow(seconds=5))
    timed_out = False
except ContextTimeout:
    timed_out = True

large = bytes(range(256)) * (64 * 1024 * 1024 // 256)
echoed = billing(CommandEcho(data=large), ResponseEcho)

print(json.dumps({
    "served_by_another_process": answer.pid != os.getpid(),
    "total": str(answer.total),
    "pdf_size": answer.pdf_size,
    "issued_by": answer.issued_by,
    "refused": refused,
    "timed_out": timed_out,
    "large_size": len(echoed.data),
    "large_sent": hashlib.sha256(large).hexdigest(),
    "large_back": hashlib.sha256(echoed.data).hexdigest(),
    "on_the_wire": len(wire),
    "all_json": all(one.startswith(b"{") for one in wire),
    "any_pickle": any(one.startswith(b"\\x80") for one in wire),
}))
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _listening(address: str, within: float) -> None:
    host, _, port = address.rpartition(":")
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex((host, int(port))) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"nothing listened at {address} within {within}s")


@pytest.fixture
def context_on_disk(tmp_path: Path) -> Path:
    (tmp_path / "billing_two_processes.py").write_text(CONTEXT)
    for transport, source in SERVERS.items():
        (tmp_path / f"server_{transport}.py").write_text(source)
    (tmp_path / "client.py").write_text(CLIENT)
    return tmp_path


@pytest.fixture(params=sorted(SERVERS))
def service_b(request: pytest.FixtureRequest, context_on_disk: Path) -> Iterator[str]:
    address = f"127.0.0.1:{_free_port()}"
    environment = {**os.environ, "PYTHONPATH": str(context_on_disk)}
    environment.pop("SINCPRO_CONTEXT_MAP", None)
    server = subprocess.Popen(
        [sys.executable, str(context_on_disk / f"server_{request.param}.py"), address],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _listening(address, 30)
        yield f"{request.param}://{address}"
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_service_configured_only_by_its_environment_is_answered_by_the_other(
    context_on_disk: Path, service_b: str
):
    environment = {
        **os.environ,
        "PYTHONPATH": str(context_on_disk),
        "SINCPRO_CONTEXT_MAP": f"billing-two-processes={service_b}?timeout=3",
    }

    finished = subprocess.run(
        [sys.executable, str(context_on_disk / "client.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert finished.returncode == 0, finished.stderr[-2000:]
    said = json.loads(finished.stdout.strip().splitlines()[-1])
    assert said.pop("large_sent") == said.pop("large_back")
    assert said == {
        "served_by_another_process": True,
        "total": "12.50",
        "pdf_size": 256 * 400,
        "issued_by": 7,
        "refused": "over the credit limit",
        "timed_out": True,
        "large_size": 64 * 1024 * 1024,
        "on_the_wire": 7,  # four commands, two responses, one domain error
        "all_json": True,
        "any_pickle": False,
    }
