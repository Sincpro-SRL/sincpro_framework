"""What auth publishes for an audit trail: every grant written, every session begun and ended, a
replayed refresh token, a locked account, a one-time link, a refused call — each a versioned
`DomainEvent` naming who it is about and who did it, through the framework's own `Publisher`
when a project hands one; nothing without one."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AccessRefused,
    AuthEvent,
    Identity,
    InMemoryRevocations,
    Permission,
    PermissionDenied,
    Unauthenticated,
    WhenDenied,
    security,
)
from sincpro_framework.auth.accounts import (
    AccountLocked,
    InMemoryRefreshTokens,
    LoginAttempts,
    OneTimeTokenIssued,
    OneTimeTokenRedeemed,
    OneTimeTokens,
    RefreshTokenReused,
    SignedIn,
    SignedOut,
    SignedOutEverywhere,
    SigningKeys,
    TokenIssuer,
)
from sincpro_framework.auth.grants import (
    Entitlement,
    EntitlementWithdrawn,
    GrantCatalog,
    InMemoryGrants,
    LevelChanged,
    Privilege,
    Role,
    RoleAssigned,
    RoleUnassigned,
    TenantEntitled,
)
from sincpro_framework.auth.grants.services.administration import GrantAdministration
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue


class P(Permission):
    READ = "billing.invoice.read"
    APPROVE = "billing.payment.approve"


class Recorder:
    """A publisher that keeps what it was handed."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any, /) -> None:
        self.events.append(event)


invoicing = Privilege("invoicing", "Invoicing")
clerk = Role("billing.clerk", "Clerk", grants={P.READ}, privilege=invoicing, sequence=10)
approver = Role(
    "billing.approver",
    "Approver",
    grants={P.APPROVE},
    implies=(clerk,),
    privilege=invoicing,
    sequence=20,
)
catalog = GrantCatalog([approver], entitlements=[Entitlement("payments", gates={P.APPROVE})])
ADMIN = Identity.user(
    "user:admin",
    tenant="bo",
    roles={"billing.approver", "billing.clerk"},
    permissions={P.READ, P.APPROVE},
)
"""The administrator of tenant bo — it holds what it gives."""


def test_every_grant_written_is_published_by_whom_and_on_whom() -> None:
    audit = Recorder()
    admin = GrantAdministration(catalog, InMemoryGrants(), InMemoryRevocations(), audit)
    friday = datetime(2026, 11, 27, tzinfo=UTC)
    with security.as_identity(ADMIN):
        admin.assign("user:ana", "bo", "billing.clerk", valid_until=friday)
        admin.unassign("user:ana", "bo", "billing.clerk")
        admin.set_level("user:ana", "bo", "invoicing", "billing.approver")
    with security.as_system("the platform sells payments to bo"):
        admin.entitle("bo", "payments")
        admin.withdraw("bo", "payments")
    assert [type(one) for one in audit.events] == [
        RoleAssigned,
        RoleUnassigned,
        LevelChanged,
        TenantEntitled,
        EntitlementWithdrawn,
    ]
    assigned, _, level, entitled, _ = audit.events
    assert (assigned.entity_type, assigned.entity_id, assigned.tenant, assigned.role) == (
        "auth.subject",
        "user:ana",
        "bo",
        "billing.clerk",
    )
    assert assigned.valid_until == friday and assigned.actor == "user:admin"
    assert (level.privilege, level.level) == ("invoicing", "billing.approver")
    assert (entitled.entity_type, entitled.entity_id, entitled.entitlement) == (
        "auth.tenant",
        "bo",
        "payments",
    )
    assert entitled.actor == "system"
    assert all(one.name.startswith("auth.") and ".v1." in one.name for one in audit.events)


def test_nothing_is_published_without_a_publisher_and_nobody_administers() -> None:
    with security.as_system("a migration"):
        GrantAdministration(catalog, InMemoryGrants()).assign(
            "user:ana", "bo", "billing.clerk"
        )
    audit = Recorder()
    with pytest.raises(Unauthenticated):
        GrantAdministration(catalog, InMemoryGrants(), publisher=audit).assign(
            "user:ana", "bo", "billing.clerk"
        )
    assert audit.events == []


class CommandApprove(DataTransferObject):
    number: str


def guarded(audit: Recorder, when_denied: WhenDenied = WhenDenied.RAISE) -> UseFramework:
    auth = AccessControl[P](publisher=audit)
    bus = UseFramework("audited-billing", log_after_execution=False)

    @bus.feature(CommandApprove)
    @auth.requires(P.APPROVE, when_denied=when_denied)
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> None:
            return None

    auth.on(bus)
    return bus


def test_a_refused_call_is_published_with_what_it_lacked() -> None:
    audit = Recorder()
    bus = guarded(audit)
    with security.as_identity(Identity.user("user:ana", tenant="bo", permissions={P.READ})):
        with pytest.raises(PermissionDenied):
            bus(CommandApprove(number="F-1"))
    with pytest.raises(Unauthenticated):
        bus(CommandApprove(number="F-1"))
    denied, nobody = audit.events
    assert isinstance(denied, AccessRefused) and denied.use_case == "CommandApprove"
    assert (denied.kind, denied.requirement, denied.actor, denied.tenant) == (
        "permission_denied",
        "billing.payment.approve",
        "user:ana",
        "bo",
    )
    assert (nobody.kind, nobody.actor, nobody.entity_id) == (
        "unauthenticated",
        None,
        "anonymous",
    )


def test_a_skipped_call_is_not_a_refusal_to_audit() -> None:
    audit = Recorder()
    with security.as_identity(Identity.user("user:ana")):
        assert guarded(audit, WhenDenied.SKIP)(CommandApprove(number="F-1")) is None
    assert audit.events == []


def test_an_every_call_refusal_is_published_too() -> None:
    audit = Recorder()
    auth = AccessControl[P](publisher=audit)
    auth.every_call(lambda call: False, name="ledger open")
    bus = UseFramework("closed-ledger", log_after_execution=False)

    @bus.feature(CommandApprove)
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> None:
            return None

    auth.on(bus)
    with security.as_identity(Identity.user("user:ana")), pytest.raises(PermissionDenied):
        bus(CommandApprove(number="F-1"))
    assert audit.events[0].requirement == "ledger open"


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def test_sessions_begun_ended_and_replayed_are_published_never_a_token() -> None:
    audit, clock = Recorder(), Clock()
    moment = lambda: datetime.fromtimestamp(clock.now, UTC)  # noqa: E731 — the test's clock
    issuer = TokenIssuer(
        "https://colegio.bo",
        audience="colegio",
        keys=SigningKeys.hmac({"2026-09": "a token secret of thirty-two bytes!"}),
        refresh_tokens=InMemoryRefreshTokens(now=moment),
        revocations=InMemoryRevocations(now=moment),
        clock=clock,
        publisher=audit,
    )
    ana = Identity.user("user:ana", tenant="bo")
    first = issuer.issue(ana, scopes=["grades"])
    assert first.refresh_token is not None
    spent = first.refresh_token.get_secret_value()
    issuer.refresh(spent)
    clock.now += 60
    with pytest.raises(Unauthenticated):
        issuer.refresh(spent)  # replayed past its grace
    second = issuer.issue(ana)
    assert second.refresh_token is not None
    issuer.revoke(second.refresh_token.get_secret_value())
    issuer.issue(ana)
    issuer.revoke_subject("user:ana")
    kinds = [type(one) for one in audit.events]
    assert kinds == [
        SignedIn,
        RefreshTokenReused,
        SignedIn,
        SignedOut,
        SignedIn,
        SignedOutEverywhere,
    ]
    signed_in, reused = audit.events[0], audit.events[1]
    assert (signed_in.entity_id, signed_in.tenant, signed_in.scopes) == (
        "user:ana",
        "bo",
        ["grades"],
    )
    assert reused.session == signed_in.session == first.session
    assert audit.events[-1].entity_id == "user:ana"
    assert all(spent not in repr(one) for one in audit.events)


def test_a_locked_account_and_a_one_time_link_are_published_never_the_link() -> None:
    audit = Recorder()
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=3, publisher=audit)
    for _ in range(3):
        with pytest.raises(Unauthenticated), attempts.attempt("ana@colegio.bo"):
            raise Unauthenticated("wrong username or password")
    links = OneTimeTokens(InMemoryKeyValue(), publisher=audit)
    link = links.issue("password-reset", "user:ana", timedelta(hours=1))
    links.redeem("password-reset", link)
    locked, issued, redeemed = audit.events
    assert isinstance(locked, AccountLocked) and locked.entity_id == "ana@colegio.bo"
    assert locked.locked_seconds == 900
    assert isinstance(issued, OneTimeTokenIssued) and isinstance(
        redeemed, OneTimeTokenRedeemed
    )
    assert (redeemed.entity_id, redeemed.purpose) == ("user:ana", "password-reset")
    assert all(link not in repr(one) for one in audit.events)


class CommandGrantClerk(DataTransferObject):
    subject: str


def test_through_the_frameworks_publisher_an_audit_context_keeps_them_in_the_flow() -> None:
    kept: list[AuthEvent] = []
    audit = UseFramework("audit", log_after_execution=False)

    @audit.feature([RoleAssigned, AccessRefused])
    class Keep(Feature):
        def execute(self, dto: AuthEvent) -> None:
            kept.append(dto)

    publisher = Publisher(SyncQueue(Subscriber(audit)))
    admin = GrantAdministration(catalog, InMemoryGrants(), publisher=publisher)
    people = UseFramework("people", log_after_execution=False)

    @people.feature(CommandGrantClerk)
    class GrantClerk(Feature):
        def execute(self, dto: CommandGrantClerk) -> None:
            admin.assign(dto.subject, "bo", "billing.clerk")

    with security.as_identity(ADMIN):
        people(CommandGrantClerk(subject="user:ana"))
    (assigned,) = kept
    assert isinstance(assigned, RoleAssigned) and assigned.actor == "user:admin"
    assert assigned.causation_id is not None and assigned.correlation_id is not None
