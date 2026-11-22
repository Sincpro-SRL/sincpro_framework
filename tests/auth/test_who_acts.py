"""Who acts and where, as a record's facts name it: the identity an `AccessControl` let in is the
execution's `user_id` and `tenant_id` — what `Entity.record()` stamps as `actor` and
`created_by`, what a draft or a cached answer is owned by —, never what the caller's headers
carried. The project's own provider, and a scope that names one, still win.

The incident: a bus guarded by `AccessControl` authenticated every caller and still recorded
every fact with no `actor`, silently.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Identity,
    Permission,
    PermissionDenied,
    security,
)
from sincpro_framework.context import ContextKey, EntrypointKind, use_context
from sincpro_framework.entrypoints.infrastructure.http import merge_http_context

ANA = Identity.user("ana")


class DeskPermission(Permission):
    OPEN = "desk.ticket.open"


class CommandWhoActs(DataTransferObject):
    pass


class Answer(DataTransferObject):
    user_id: str | None = None


def _desk(gives_user_id: Any = None) -> UseFramework:
    bus = UseFramework("desk-who-acts", log_after_execution=False)
    auth = AccessControl[DeskPermission]()
    auth.on(bus)
    if gives_user_id is not None:
        bus.context_provider(gives=[ContextKey.USER_ID])(gives_user_id)

    @bus.feature(CommandWhoActs)
    class WhoActs(Feature):
        def execute(self, dto: CommandWhoActs) -> Answer:
            return Answer(user_id=use_context().get(ContextKey.USER_ID))

    return bus


def _who(bus: UseFramework) -> str | None:
    answer = bus(CommandWhoActs(), Answer)
    assert isinstance(answer, Answer)
    return answer.user_id


def test_the_identity_let_in_is_who_acts():
    with security.as_identity(ANA):
        assert _who(_desk()) == "ana"


def test_nobody_acts_for_an_anonymous_caller():
    assert _who(_desk()) is None


def test_the_projects_provider_wins_over_the_identity():
    def profile_of(context: Mapping[Any, Any]) -> Mapping[str, str]:
        return {ContextKey.USER_ID: "profile:7"}

    with security.as_identity(ANA):
        assert _who(_desk(profile_of)) == "profile:7"


def test_a_scope_that_names_who_acts_wins_over_the_identity():
    bus = _desk()

    with security.as_identity(ANA), bus.context({ContextKey.USER_ID: "batch"}):
        assert _who(bus) == "batch"


class Seen(DataTransferObject):
    user_id: str | None = None
    tenant_id: str | None = None


def _ledger() -> UseFramework:
    bus = UseFramework("ledger-who-and-where", log_after_execution=False)
    AccessControl[DeskPermission]().on(bus)

    @bus.feature(CommandWhoActs)
    class WhereActs(Feature):
        def execute(self, dto: CommandWhoActs) -> Seen:
            context = use_context()
            return Seen(
                user_id=context.get(ContextKey.USER_ID),
                tenant_id=context.get(ContextKey.TENANT_ID),
            )

    return bus


def _carried(bus: UseFramework, identity: Identity, headers: Mapping[str, str]) -> Seen:
    """A call as a wire opens it: what the caller's headers carried, as the identity verified."""
    carried = merge_http_context(headers)
    with security.as_identity(identity), bus.context(carried, kind=EntrypointKind.REST):
        seen = bus(CommandWhoActs(), Seen)
    assert isinstance(seen, Seen)
    return seen


ANA_BO = Identity.user("user:ana", tenant="bo")


def test_who_a_caller_carried_never_replaces_the_person_verified():
    """The incident: `Baggage: user_id=user:admin` beside ana's token made every fact, draft and
    cached answer of the call ana's in name only."""
    seen = _carried(_ledger(), ANA_BO, {"Baggage": "user_id=user:admin"})
    assert seen.user_id == "user:ana"


def test_a_tenant_a_caller_carried_that_is_not_its_own_is_refused():
    """The incident: a user of `bo` sent `tenant_id=pe` and acted on `pe`'s data with `bo`'s
    permissions."""
    with pytest.raises(PermissionDenied, match="acts in tenant 'bo', the call names 'pe'"):
        _carried(_ledger(), ANA_BO, {"Baggage": "tenant_id=pe"})
    with pytest.raises(PermissionDenied):
        _carried(_ledger(), ANA_BO, {"sincpro-context": '{"tenant_ids": ["bo", "pe"]}'})


def test_the_tenant_of_the_identity_is_where_the_call_acts():
    assert _carried(_ledger(), ANA_BO, {}).tenant_id == "bo"
    assert _carried(_ledger(), ANA_BO, {"Baggage": "tenant_id=bo"}).tenant_id == "bo"


def test_a_service_acts_for_whom_its_call_names():
    billing = Identity.service("billing")
    assert _carried(_ledger(), billing, {"Baggage": "user_id=user:ana"}).user_id == "user:ana"


def test_code_still_says_where_a_call_acts():
    bus = _ledger()
    with security.as_identity(ANA_BO), bus.context({ContextKey.TENANT_ID: "pe"}):
        assert bus(CommandWhoActs(), Seen).tenant_id == "pe"


def test_an_anonymous_caller_owns_nothing_it_names():
    """The incident: on an open use case, `Baggage: user_id=user:ana,tenant_id=bo` from a caller
    nobody verified made the call's draft, cached answer and idempotent write ana's."""
    nobody = Identity.anonymous()
    seen = _carried(_ledger(), nobody, {"Baggage": "user_id=user:ana,tenant_id=bo"})
    assert (seen.user_id, seen.tenant_id) == (None, None)
    seen = _carried(_ledger(), nobody, {"sincpro-context": '{"tenant_ids": ["bo", "pe"]}'})
    assert seen.tenant_id is None


def test_an_anonymous_caller_never_reads_the_answer_cached_for_whom_it_names():
    bus = UseFramework("rates-who-acts", log_after_execution=False)
    AccessControl[DeskPermission]().on(bus)
    computed: list[str | None] = []

    @bus.cache
    def balance() -> str:
        computed.append(use_context().get(ContextKey.USER_ID))
        return f"balance of {computed[-1]}"

    class Balance(DataTransferObject):
        said: str

    @bus.feature(CommandWhoActs)
    class ReadBalance(Feature):
        def execute(self, dto: CommandWhoActs) -> Balance:
            return Balance(said=balance())

    with security.as_identity(ANA_BO), bus.context({}, kind=EntrypointKind.REST):
        assert bus(CommandWhoActs(), Balance).said == "balance of user:ana"
    carried = merge_http_context({"Baggage": "user_id=user:ana,tenant_id=bo"})
    with bus.context(carried, kind=EntrypointKind.REST):
        assert bus(CommandWhoActs(), Balance).said == "balance of None"
    assert computed == ["user:ana", None]


def test_an_access_control_turned_off_leaves_an_anonymous_callers_context_as_it_came():
    bus = UseFramework("ledger-unguarded-who", log_after_execution=False)
    AccessControl[DeskPermission](enabled=False).on(bus)

    @bus.feature(CommandWhoActs)
    class WhereActs(Feature):
        def execute(self, dto: CommandWhoActs) -> Seen:
            return Seen(user_id=use_context().get(ContextKey.USER_ID))

    seen = _carried(bus, Identity.anonymous(), {"Baggage": "user_id=user:dev"})
    assert seen.user_id == "user:dev"
