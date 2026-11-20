"""Who acts, as a record's facts name it: the identity an `AccessControl` let in is the
execution's `user_id` when nothing said one — what `Entity.record()` stamps as `actor` and
`created_by`. The project's own provider, and a scope that names one, still win.

The incident: a bus guarded by `AccessControl` authenticated every caller and still recorded
every fact with no `actor`, silently.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, security
from sincpro_framework.context import ContextKey, use_context

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
