"""Declared exposure, on the use case (PRD_14 §4, §10): a decorator records a frozen binding by
class and hands the class back untouched, so the order the lines are written in never changes
what is published — and a `services/` module that declares its exposure imports no transport.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints import internal as internal_from_entrypoints
from sincpro_framework.entrypoints.exposure import (
    Binding,
    Deprecation,
    ExposureRefused,
    McpBinding,
    QueueBinding,
    RestBinding,
    bindings_of,
    declare,
    grpc,
    internal,
    is_internal,
    mcp,
    queue,
    rest,
    rpc,
)

ROOT = Path(__file__).resolve().parents[2]


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class ResponseIssueInvoice(DataTransferObject):
    number: str


def test_decorators_record_by_class_and_return_it_untouched():
    class Plain(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    @rest.post("/invoices", status=201)
    @rpc()
    @grpc()
    @mcp(title="Emitir factura", destructive=False)
    @queue.consumes("billing.invoices.issue", producers=("svc:sales",))
    class Bound(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    assert set(Bound.__dict__) == set(Plain.__dict__)
    assert set(bindings_of(Bound)) == {"rest", "rpc", "grpc", "mcp", "queue"}
    assert bindings_of(Plain) == {}


def test_the_order_decorators_are_written_in_does_not_change_the_bindings():
    @rest.post("/invoices", status=201)
    @mcp(destructive=False)
    class Written(Feature):
        pass

    @mcp(destructive=False)
    @rest.post("/invoices", status=201)
    class Reordered(Feature):
        pass

    assert bindings_of(Written) == bindings_of(Reordered)


def test_a_second_binding_for_one_wire_is_refused_where_it_is_written():
    with pytest.raises(ExposureRefused, match="two rest bindings"):

        @rest.get("/invoices")
        @rest.post("/invoices")
        class Twice(Feature):
            pass


def test_the_verbs_fix_the_method_and_rest_leaves_it_to_be_derived():
    @rest.patch("/invoices/{invoice_id}", body="merge-patch", concurrency="if-match")
    class Fixed(Feature):
        pass

    @rest("/invoices")
    class Derived(Feature):
        pass

    fixed = bindings_of(Fixed)["rest"]
    assert isinstance(fixed, RestBinding)
    assert (fixed.method, fixed.body, fixed.concurrency) == (
        "PATCH",
        "merge-patch",
        "if-match",
    )
    assert bindings_of(Derived)["rest"].model_dump()["method"] is None


def test_a_decorator_records_only_what_it_was_told():
    """The composition merges field by field: a default the decorator never said must not
    look declared, or it would override a group's convention it never meant to."""

    @rest.post("/invoices", status=201)
    class Issue(Feature):
        pass

    assert bindings_of(Issue)["rest"].model_fields_set == {"method", "path", "status"}


def test_a_binding_is_frozen_and_refuses_a_field_it_does_not_have():
    binding = RestBinding(path="/invoices")

    with pytest.raises(ValidationError):
        binding.path = "/other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RestBinding(stauts=201)  # type: ignore[call-arg]


def test_deprecated_true_is_a_deprecation_with_no_dates():
    @rpc(deprecated=True)
    @mcp(deprecated=Deprecation(since=date(2026, 1, 1), replacement=CommandIssueInvoice))
    class Old(Feature):
        pass

    assert bindings_of(Old)["rpc"].deprecated == Deprecation()
    assert bindings_of(Old)["mcp"].deprecated == Deprecation(
        since=date(2026, 1, 1), replacement=CommandIssueInvoice
    )


def test_queue_consumes_and_hears_are_one_queue_binding():
    @queue.hears(group="accounting")
    class Hear(Feature):
        pass

    heard = bindings_of(Hear)["queue"]
    assert isinstance(heard, QueueBinding)
    assert (heard.kind, heard.group) == ("hears", "accounting")


def test_a_replaced_handler_inherits_the_bindings_it_declares_none_for():
    """Replacing a handler never moves or drops a public operation — wire by wire."""
    bus = UseFramework("replacing-exposure", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @rest.post("/invoices", status=201)
    @mcp(destructive=False)
    class Issue(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    @bus.feature(CommandIssueInvoice, replaces=Issue)
    @mcp(title="Emitir", destructive=False)
    class IssueV2(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-2")

    inherited = bindings_of(IssueV2, bus.replaced_for(CommandIssueInvoice))

    assert inherited["rest"] == bindings_of(Issue)["rest"]
    assert isinstance(inherited["mcp"], McpBinding)
    assert inherited["mcp"].title == "Emitir"


def test_a_project_binding_is_recorded_the_same_way():
    class CliBinding(Binding):
        wire = "cli"
        command: str | None = None

    class Handler(Feature):
        pass

    declare(Handler, CliBinding(command="issue"))

    assert bindings_of(Handler) == {"cli": CliBinding(command="issue")}


def test_internal_is_still_importable_from_where_it_was():
    @internal_from_entrypoints
    class Hidden(Feature):
        pass

    assert internal is internal_from_entrypoints
    assert is_internal(Hidden)


def test_declaring_exposure_imports_no_transport_library():
    """A `services/` module declares its exposure on every wire — and still needs none of
    Starlette, FastAPI, grpc, FastMCP or FastStream."""
    program = """
import sys
for name in ("starlette", "fastapi", "uvicorn", "grpc", "google.protobuf", "fastmcp",
             "faststream", "sqlalchemy", "opentelemetry", "sentry_sdk"):
    sys.modules[name] = None

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.exposure import grpc, mcp, queue, rest, rpc, bindings_of

class CommandPing(DataTransferObject):
    n: int

bus = UseFramework("exposure-without-extras", log_after_execution=False)

@bus.feature(CommandPing)
@rest.post("/pings")
@rpc()
@grpc()
@mcp()
@queue.consumes("pings")
class Ping(Feature):
    def execute(self, dto: CommandPing) -> None:
        return None

assert set(bindings_of(Ping)) == {"rest", "rpc", "grpc", "mcp", "queue"}

from sincpro_framework.entrypoints import Gateway

class RestSurface(Gateway):
    wire = "rest"

assert [one.binding.path for one in RestSurface([bus], unguarded=True).surface()] == ["/pings"]
print("ok")
"""
    ran = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, cwd=ROOT, check=False
    )

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip().endswith("ok")
