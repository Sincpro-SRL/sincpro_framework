"""The build refuses a surface that cannot hold (PRD_14 §7, §10; PRD_15 §2.2) — before anything
is served, every reason at once. Each rule here is an incident it prevents: a path that 404s
after a rename, a public use case nobody said who may call, a GET that writes, a replayed read.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Permission
from sincpro_framework.caching import Idempotency, InMemoryKeyValue
from sincpro_framework.ddd import DomainEvent, Query
from sincpro_framework.entrypoints import ExposureRefused, Gateway
from sincpro_framework.entrypoints.exposure import (
    Deprecation,
    RestBinding,
    internal,
    mcp,
    queue,
    rest,
    rpc,
)


class RestSurface(Gateway):
    wire = "rest"


class RpcSurface(Gateway):
    wire = "rpc"


class BillingPermission(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class ResponseIssueInvoice(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class ResponseInvoice(DataTransferObject):
    number: str


def _bus(declare_on_handler, command: type = CommandIssueInvoice, answers: object = None):
    """One use case answering `command`, its handler decorated by `declare_on_handler`."""
    bus = UseFramework("validated-billing", log_after_execution=False)
    response = ResponseIssueInvoice if answers is None else answers

    class Handler(Feature):
        def execute(self, dto: DataTransferObject) -> response:  # type: ignore[valid-type]
            return None

    bus.feature(command)(declare_on_handler(Handler))
    return bus


def _refused(gateway: Gateway) -> str:
    with pytest.raises(ExposureRefused) as refused:
        gateway.surface()
    return str(refused.value)


def test_a_surface_that_holds_builds():
    gateway = RestSurface([_bus(rest.post("/invoices", status=201))], unguarded=True)

    assert gateway.verify() == []
    assert len(gateway.surface()) == 1


# --- @internal, orphans ---------------------------------------------------------------------


def test_internal_with_a_binding_is_refused():
    bus = _bus(lambda cls: internal(rest.post("/invoices")(cls)))

    assert "@internal and bound on rest" in _refused(RestSurface([bus], unguarded=True))


def test_internal_on_the_command_with_a_bind_is_refused():
    @internal
    class CommandHidden(DataTransferObject):
        pass

    bus = _bus(lambda cls: cls, command=CommandHidden)
    gateway = RestSurface([bus], unguarded=True).bind(CommandHidden, RestBinding(path="/x"))

    assert "@internal and bound on rest" in _refused(gateway)


def test_an_override_for_a_command_no_bus_answers_is_refused():
    class CommandNobodyAnswers(DataTransferObject):
        pass

    gateway = RestSurface([_bus(rest.post("/invoices"))], unguarded=True)
    gateway.override(CommandNobodyAnswers, status=202)

    assert "no bus of this gateway answers it" in _refused(gateway)


# --- the fallback access rule ---------------------------------------------------------------


def test_a_bound_use_case_on_a_guarded_bus_must_declare_its_access():
    auth = AccessControl[BillingPermission]()
    bus = _bus(rest.post("/invoices"))
    auth.on(bus)

    assert "declares neither @auth.requires nor @auth.public" in _refused(RestSurface([bus]))


@pytest.mark.parametrize("declared", ["requires", "public"])
def test_a_guarded_use_case_that_declares_its_access_is_published(declared):
    auth = AccessControl[BillingPermission]()
    access = auth.requires(BillingPermission.ISSUE) if declared == "requires" else auth.public
    bus = _bus(lambda cls: access(rest.post("/invoices")(cls)))
    auth.on(bus)

    assert len(RestSurface([bus]).surface()) == 1


def test_an_unguarded_bus_is_refused_unless_the_gateway_is_told():
    bus = _bus(rest.post("/invoices"))

    assert "has no AccessControl" in _refused(RestSurface([bus]))
    assert len(RestSurface([bus], unguarded=True).surface()) == 1


# --- REST facts -----------------------------------------------------------------------------


def test_a_path_field_that_is_not_a_command_field_is_refused():
    bus = _bus(rest.post("/customers/{id}/invoices"))

    said = _refused(RestSurface([bus], unguarded=True))

    assert "names {id}, which is not a field of CommandIssueInvoice" in said


def test_a_location_field_that_is_not_a_response_field_is_refused():
    fine = _bus(rest.post("/invoices", status=201, location="/invoices/{number}"))
    wrong = _bus(rest.post("/invoices", status=201, location="/invoices/{invoice_id}"))

    assert RestSurface([fine], unguarded=True).verify() == []
    assert "location /invoices/{invoice_id} names {invoice_id}" in _refused(
        RestSurface([wrong], unguarded=True)
    )


def test_get_on_a_command_that_is_not_a_query_is_refused():
    write = _bus(rest.get("/invoices"))
    read = _bus(
        rest.get("/invoices/{invoice_id}"), command=QueryInvoice, answers=ResponseInvoice
    )

    assert "GET on a Command that is not a Query" in _refused(
        RestSurface([write], unguarded=True)
    )
    assert RestSurface([read], unguarded=True).verify() == []


def test_delete_or_204_with_a_body_is_refused():
    deleting = _bus(rest.delete("/invoices/{customer_id}"))
    no_content = _bus(rest.post("/invoices", status=204))
    answers_none = _bus(rest.delete("/invoices/{customer_id}"), answers=type(None))

    assert "answers no body" in _refused(RestSurface([deleting], unguarded=True))
    assert "answers no body" in _refused(RestSurface([no_content], unguarded=True))
    assert RestSurface([answers_none], unguarded=True).verify() == []


def test_a_patch_with_required_fields_needs_merge_patch():
    class CommandRename(DataTransferObject):
        invoice_id: str
        name: str

    plain = _bus(rest.patch("/invoices/{invoice_id}"), command=CommandRename)
    merge = _bus(
        rest.patch("/invoices/{invoice_id}", body="merge-patch"), command=CommandRename
    )

    assert "declare body='merge-patch'" in _refused(RestSurface([plain], unguarded=True))
    assert RestSurface([merge], unguarded=True).verify() == []


def test_the_composition_is_validated_too():
    """An override is the last word — and the build checks the word it had."""
    gateway = RestSurface([_bus(rest.post("/invoices"))], unguarded=True)
    gateway.override(CommandIssueInvoice, path="/invoices/{number}")

    assert "names {number}, which is not a field of CommandIssueInvoice" in _refused(gateway)


# --- the other wires' facts, checked by any gateway ------------------------------------------


def test_an_mcp_hint_that_contradicts_the_command_kind_is_refused():
    writes_read_only = _bus(mcp(read_only=True))
    query_writing = _bus(mcp(read_only=False), command=QueryInvoice, answers=ResponseInvoice)

    assert "read_only=True on a Command that is not a Query" in "\n".join(
        RestSurface([writes_read_only], unguarded=True).verify()
    )
    assert "read_only=False on a Query" in "\n".join(
        RestSurface([query_writing], unguarded=True).verify()
    )


def test_an_rpc_name_under_the_reserved_prefix_is_refused():
    bus = _bus(rpc("rpc.issue_invoice"))

    assert "the rpc. prefix is reserved" in _refused(RpcSurface([bus], unguarded=True))


def test_a_deprecation_past_its_sunset_is_refused():
    past = Deprecation(sunset=date.today() - timedelta(days=1))
    future = Deprecation(sunset=date.today() + timedelta(days=90))

    gone = _bus(rest.post("/invoices", deprecated=past))
    going = _bus(rest.post("/invoices", deprecated=future))

    assert "is past — remove the binding" in _refused(RestSurface([gone], unguarded=True))
    assert RestSurface([going], unguarded=True).verify() == []


def test_a_queue_binding_that_contradicts_the_message_kind_is_refused():
    @dataclass(kw_only=True)
    class InvoiceIssued(DomainEvent):
        number: str = ""

    consumes_event = _bus(queue.consumes("billing.invoices"), command=InvoiceIssued)
    hears_command = _bus(queue.hears(group="accounting"))

    assert "consumes a DomainEvent" in "\n".join(
        RestSurface([consumes_event], unguarded=True).verify()
    )
    assert "hears a Command" in "\n".join(
        RestSurface([hears_command], unguarded=True).verify()
    )


# --- stages (§10) ---------------------------------------------------------------------------


def test_idempotency_once_on_a_query_is_refused():
    idempotency = Idempotency(InMemoryKeyValue())
    once = idempotency.once(expires_after=timedelta(minutes=5))
    on_query = _bus(
        lambda cls: once(rest.get("/invoices/{invoice_id}")(cls)),
        command=QueryInvoice,
        answers=ResponseInvoice,
    )
    on_command = _bus(lambda cls: once(rest.post("/invoices")(cls)))

    assert "@idempotency.once on a Query" in _refused(RestSurface([on_query], unguarded=True))
    assert RestSurface([on_command], unguarded=True).verify() == []
    assert RestSurface([on_command], unguarded=True).surface()[0].operation.idempotent


# --- fail closed ----------------------------------------------------------------------------


def test_every_reason_is_said_at_once_and_nothing_is_published():
    bus = _bus(lambda cls: rpc("rpc.x")(rest.get("/customers/{id}")(cls)))
    gateway = RestSurface([bus])

    said = _refused(gateway)

    assert "names {id}" in said
    assert "GET on a Command that is not a Query" in said
    assert "rpc. prefix is reserved" in said
    assert "has no AccessControl" in said
    with pytest.raises(ExposureRefused):
        gateway.operations()
    with pytest.raises(ExposureRefused):
        gateway.manifest()
