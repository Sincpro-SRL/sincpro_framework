"""A record of any bounded context named by one text, and read back by its own `Get`.

The cases are a payment that comes back hours later with the ref it was sent with: billing's
invoices, read by whoever may read invoices; members, read by anyone signed in; a vault whose
reads declare nothing on a strict bus. Whoever resolves a ref is decided by the record's own
reads — never lent the permission of the use case that asked.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.auth import AccessControl, Identity, Permission, security
from sincpro_framework.auth.domain import exceptions as auth_exceptions
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Entity,
    EntityReads,
    Get,
    GetMany,
    ResponseRecord,
    ResponseRecords,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.references import (
    EntityRef,
    EntityRefResolution,
    EntityRefs,
    Resolve,
    ResponseResolve,
)


class BillingPermission(Permission):
    READ_INVOICES = "billing.invoice.read"


@dataclass
class Invoice(Entity):
    number: str = ""

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:
        return "number"


@dataclass
class Member(Entity):
    name: str = ""


@dataclass
class Secret(Entity):
    name: str = ""


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class ResponseInvoices(ResponseRecords):
    invoices: list[Invoice]


class QueryGetInvoice(Get[Invoice, ResponseInvoice]):
    pass


class QueryGetInvoices(GetMany[Invoice, ResponseInvoices]):
    pass


class ResponseMember(ResponseRecord):
    member: Member


class QueryGetMember(Get[Member, ResponseMember]):
    pass


class ResponseSecret(ResponseRecord):
    secret: Secret


class QueryGetSecret(Get[Secret, ResponseSecret]):
    pass


class QueryResolve(Resolve):
    pass


READER = Identity.user("user:1", permissions={BillingPermission.READ_INVOICES})
SIGNED_IN = Identity.user("user:2")


@dataclass
class World:
    refs: EntityRefs
    platform: UseFramework
    invoice: Invoice
    member: Member
    secret: Secret


@pytest.fixture
def world() -> World:
    billing_auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)
    invoices = MemoryRepository()
    billing.add_dependency("repository", invoices)

    @billing.feature([QueryGetInvoice, QueryGetInvoices])
    @billing_auth.requires(BillingPermission.READ_INVOICES)
    class InvoiceReads(EntityReads[Invoice]):
        pass

    billing_auth.on(billing)

    members_auth = AccessControl[BillingPermission]()
    members = UseFramework("members", log_after_execution=False)
    people = MemoryRepository()
    members.add_dependency("repository", people)

    @members.feature(QueryGetMember)
    @members_auth.authenticated
    class MemberReads(EntityReads[Member]):
        pass

    members_auth.on(members)

    vault_auth = AccessControl[BillingPermission](strict=True)
    vault = UseFramework("vault", log_after_execution=False)
    secrets = MemoryRepository()
    vault.add_dependency("repository", secrets)

    @vault.feature(QueryGetSecret)
    class SecretReads(EntityReads[Secret]):  # declares nothing, on a strict bus
        pass

    vault_auth.on(vault)

    refs = EntityRefs([billing, members, vault])
    platform = UseFramework("platform", log_after_execution=False)
    platform.add_dependency("refs", refs)

    @platform.feature(QueryResolve)
    class Resolution(EntityRefResolution):
        pass

    invoice, member, secret = Invoice(number="F-0042"), Member(name="Ana Pérez"), Secret()
    invoices.save(invoice)
    people.save(member)
    secrets.save(secret)
    return World(refs, platform, invoice, member, secret)


def test_a_ref_is_one_text_naming_a_record_of_a_bounded_context():
    ref = EntityRef.parse("billing.Invoice:F:7")

    assert (ref.bounded_context, ref.entity, ref.key, str(ref)) == (
        "billing",
        "Invoice",
        "F:7",
        "billing.Invoice:F:7",
    )
    with pytest.raises(ValueError, match="bounded_context.Entity:key"):
        EntityRef.parse("Invoice:0192")


def test_a_ref_is_made_in_the_bounded_context_that_runs_or_the_one_named():
    invoice = Invoice(number="F-1")
    billing = UseFramework("billing", log_after_execution=False)

    with billing.context({}):
        made = EntityRef.of(invoice)

    assert str(made) == f"billing.Invoice:{invoice.id}"
    assert EntityRef.of(invoice, bounded_context="billing") == made
    with pytest.raises(exceptions.ProgrammingError, match="outside a bus"):
        EntityRef.of(invoice)


def test_a_ref_resolves_to_the_record_its_own_get_reads(world: World):
    with security.as_identity(READER):
        invoice = world.refs.resolve(EntityRef("billing", "Invoice", world.invoice.id))

    assert invoice.number == "F-0042"


def test_many_refs_resolve_in_the_order_asked_with_none_for_a_record_not_there(world: World):
    asked = [
        EntityRef("members", "Member", world.member.id),
        EntityRef("billing", "Invoice", world.invoice.id),
        EntityRef("billing", "Invoice", "0192ffff"),
    ]

    with security.as_identity(READER):
        member, invoice, gone = world.refs.resolve_many(asked)

    assert (member.name, invoice.number, gone) == ("Ana Pérez", "F-0042", None)


def test_resolving_never_lends_a_permission_the_caller_lacks(world: World):
    invoice = EntityRef("billing", "Invoice", world.invoice.id)
    secret = EntityRef("vault", "Secret", world.secret.id)

    with security.as_identity(SIGNED_IN), pytest.raises(auth_exceptions.PermissionDenied):
        world.platform(QueryResolve(refs=[invoice]))  # the resolution runs; the Get refuses
    with security.as_identity(READER), pytest.raises(auth_exceptions.PermissionDenied):
        world.refs.resolve(
            secret
        )  # declares nothing on a strict bus: refused as from outside


def test_a_ref_nothing_here_reads_is_not_found_naming_why(world: World):
    with pytest.raises(ddd_exceptions.AggregateNotFound, match="no bounded context 'sales'"):
        world.refs.resolve(EntityRef("sales", "Order", "1"))
    with pytest.raises(ddd_exceptions.AggregateNotFound, match="reads no Payment by a Get"):
        world.refs.resolve(EntityRef("billing", "Payment", "1"))


def test_resolve_answers_each_record_with_how_a_person_names_it(world: World):
    with security.as_identity(READER):
        answer = world.platform(
            QueryResolve(
                refs=[
                    EntityRef("billing", "Invoice", world.invoice.id),
                    EntityRef("members", "Member", world.member.id),
                ]
            ),
            ResponseResolve,
        )

    assert answer is not None
    written = answer.model_dump(mode="json")["records"]
    assert [(one["ref"], one["display"]) for one in written] == [
        (f"billing.Invoice:{world.invoice.id}", "F-0042"),
        (f"members.Member:{world.member.id}", "Ana Pérez"),
    ]
    assert written[0]["record"]["number"] == "F-0042"


def test_resolve_is_a_read_every_wire_publishes():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway
    from sincpro_framework.entrypoints.entrypoint.decorators import rest

    members = UseFramework("people", log_after_execution=False)
    people = MemoryRepository()
    members.add_dependency("repository", people)

    @members.feature(QueryGetMember)
    class MemberReads(EntityReads[Member]):
        pass

    platform = UseFramework("lookup", log_after_execution=False)
    platform.add_dependency("refs", EntityRefs([members]))

    @platform.feature(QueryResolve)
    @rest.get("/refs")
    class Resolution(EntityRefResolution):
        pass

    member = Member(name="Ana Pérez")
    people.save(member)
    client = TestClient(FastApiGateway([platform], unguarded=True).app())

    answered = client.get("/lookup/refs", params={"refs": f'["people.Member:{member.id}"]'})

    assert answered.status_code == 200, answered.text
    assert answered.json()["records"][0]["display"] == "Ana Pérez"
