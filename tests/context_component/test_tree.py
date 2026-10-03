"""The context is a tree of nodes, read from anywhere, the nearest winning (PRD_22 §3-§4).

ROOT → BUS → ENTRYPOINT → APPLICATION → FEATURE → HOOK, and SCOPE wherever a block opens one
"""

from dataclasses import dataclass
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import Context, EntrypointKind, Level, carrying, use_context
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.repositories import Hook, Hooks, MemoryRepository


class CommandConfirm(DataTransferObject):
    pass


class CommandCheck(DataTransferObject):
    pass


class CommandWrite(DataTransferObject):
    pass


class CommandRead(DataTransferObject):
    pass


def _sales(seen: list[Context]) -> UseFramework:
    sales = UseFramework("context-tree-sales", log_after_execution=False)

    @sales.feature(CommandCheck)
    class Check(Feature):
        def execute(self, dto: CommandCheck) -> None:
            seen.append(use_context())

    @sales.app_service(CommandConfirm)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirm) -> None:
            seen.append(use_context())
            self.feature_bus.execute(CommandCheck())

    return sales


# --- the levels ---------------------------------------------------------------------------------


def test_outside_every_scope_the_context_is_the_process():
    context = use_context()

    assert context.level is Level.ROOT
    assert context.parent is None and context.entrypoint is None
    assert dict(context) == {}


def test_every_layer_is_a_node_of_its_own():
    seen: list[Context] = []
    sales = _sales(seen)
    with sales.context({"tenant_id": "acme"}):
        sales(CommandConfirm())

    application, feature = seen
    assert application.level is Level.APPLICATION
    assert feature.level is Level.FEATURE
    assert [one.level for one in feature.lineage()] == [
        Level.FEATURE,
        Level.APPLICATION,
        Level.ENTRYPOINT,
        Level.BUS,
        Level.ROOT,
    ]
    assert feature.application is not None and feature.application.label == "CommandConfirm"
    assert feature.entrypoint is not None and feature.entrypoint.kind is EntrypointKind.DIRECT


def test_a_level_is_reached_by_name():
    seen: list[Context] = []
    sales = _sales(seen)
    reached: list[Any] = []

    @sales.feature(CommandRead)
    class ReadLevels(Feature):
        def execute(self, dto: CommandRead) -> None:
            reached.append(use_context(Level.ENTRYPOINT))
            reached.append(use_context(Level.APPLICATION))

    with sales.context({"tenant_id": "acme"}):
        sales(CommandRead())

    entrypoint, application = reached
    assert entrypoint is not None and entrypoint["tenant_id"] == "acme"
    assert application is None


def test_the_nearest_node_wins_and_closing_a_scope_returns_to_its_parent():
    seen: list[Context] = []
    sales = _sales(seen)
    with sales.context({"tenant_id": "acme", "lang": "es"}):
        with use_context().scoped({"tenant_id": "beta"}) as inner:
            sales(CommandCheck())
            assert inner.level is Level.SCOPE
        sales(CommandCheck())

    in_scope, after = seen
    assert (in_scope["tenant_id"], in_scope["lang"]) == ("beta", "es")
    assert after["tenant_id"] == "acme"
    origin = in_scope.origin("tenant_id")
    assert origin is not None and origin.level is Level.SCOPE
    assert in_scope.parent is not None


def test_the_process_level_is_read_by_every_flow():
    use_context().root.set("maintenance", True)
    seen: list[Context] = []
    _sales(seen)(CommandCheck())

    assert seen[0]["maintenance"] is True
    assert seen[0].origin("maintenance").level is Level.ROOT  # type: ignore[union-attr]


def test_a_value_under_its_type():
    @dataclass
    class SiatSettings:
        url: str

    use_context().root.set(SiatSettings, SiatSettings(url="https://siat"))
    seen: list[Context] = []
    _sales(seen)(CommandCheck())

    assert seen[0][SiatSettings].url == "https://siat"
    assert seen[0].get(dict) is None


def test_a_cron_and_a_worker_open_their_flow_with_a_kind():
    seen: list[Context] = []
    sales = _sales(seen)
    with carrying({"tenant_id": "acme"}, EntrypointKind.CRON):
        sales(CommandCheck())

    assert seen[0].entrypoint is not None
    assert seen[0].entrypoint.kind is EntrypointKind.CRON
    assert seen[0]["tenant_id"] == "acme"


# --- writing ------------------------------------------------------------------------------------


def test_set_reaches_what_the_node_runs_never_its_caller_or_siblings():
    seen: list[Context] = []
    sales = UseFramework("context-tree-set", log_after_execution=False)

    @sales.feature(CommandWrite)
    class Write(Feature):
        def execute(self, dto: CommandWrite) -> None:
            use_context().set("pos_id", 3)
            sales(CommandCheck())

    @sales.feature(CommandCheck)
    class Check(Feature):
        def execute(self, dto: CommandCheck) -> None:
            seen.append(use_context())

    @sales.app_service(CommandConfirm)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirm) -> None:
            self.feature_bus.execute(CommandWrite())
            seen.append(use_context())
            self.feature_bus.execute(CommandCheck())

    sales(CommandConfirm())

    child, caller, sibling = seen
    assert child["pos_id"] == 3
    assert "pos_id" not in caller and "pos_id" not in sibling


def test_a_mapping_write_reaches_the_whole_call_as_it_always_did():
    seen: list[Context] = []
    sales = UseFramework("context-tree-mapping", log_after_execution=False)

    @sales.feature(CommandWrite)
    class Write(Feature):
        def execute(self, dto: CommandWrite) -> None:
            self.context["note"] = "from-writer"

    @sales.feature(CommandCheck)
    class Check(Feature):
        def execute(self, dto: CommandCheck) -> None:
            seen.append(use_context())

    @sales.app_service(CommandConfirm)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirm) -> None:
            self.feature_bus.execute(CommandWrite())
            self.feature_bus.execute(CommandCheck())

    sales(CommandConfirm())

    assert seen[0]["note"] == "from-writer"


def test_a_scope_takes_away_what_it_inherited():
    seen: list[Context] = []
    sales = _sales(seen)
    with sales.context({"tenant_id": "acme", "lang": "es"}):
        with sales.context({}):
            del use_context()["lang"]
            sales(CommandCheck())
        sales(CommandCheck())

    assert "lang" not in seen[0]
    assert seen[1]["lang"] == "es"


def test_what_a_bus_answers_from_outside_is_read_only():
    sales = UseFramework("context-tree-readonly", log_after_execution=False)
    with sales.context({"tenant_id": "acme"}):
        found = sales.current_context()
        assert found["tenant_id"] == "acme" and isinstance(found, dict)
        with pytest.raises(TypeError):
            found["tenant_id"] = "other"


def test_writing_outside_every_scope_writes_the_process_and_says_so():
    context = use_context()
    context["flag"] = 1

    assert use_context().root["flag"] == 1


# --- hooks --------------------------------------------------------------------------------------


@dataclass
class Invoice(Entity):
    total: int = 0


def test_a_hook_runs_in_a_node_of_its_own():
    seen: list[Context] = []
    billing = UseFramework("context-tree-billing", log_after_execution=False)
    billing_hooks = Hooks(None).inject(billing)

    @billing_hooks.on(Invoice)
    class Watches(Hook):
        def before_save(self, record: Invoice) -> None:
            context = use_context()
            context.set("checked_by", "hook")
            seen.append(context)

    repository = MemoryRepository(hooks=billing_hooks)

    @billing.feature(CommandWrite)
    class Save(Feature):
        def execute(self, dto: CommandWrite) -> None:
            repository.save(Invoice(total=1))
            seen.append(use_context())

    billing(CommandWrite())

    hook, feature = seen
    assert hook.level is Level.HOOK and hook.label == "Watches"
    assert hook.feature is not None and hook.feature.label == "CommandWrite"
    assert "checked_by" not in feature


# --- the flow, said by the context --------------------------------------------------------------


def _flow_of(mode: str) -> list[tuple[str, str]]:
    """What a Feature an ApplicationService runs says its flow is, after the service changed it."""
    from sincpro_framework.context import current_execution
    from sincpro_framework.observability.correlation import execution_context

    bus = UseFramework(f"context-flow-{mode}", log_after_execution=False)
    seen: list[tuple[str, str]] = []

    @bus.feature(CommandCheck)
    class Check(Feature):
        def execute(self, dto: CommandCheck) -> None:
            running = current_execution()
            assert running is not None
            seen.append((running.correlation_id, execution_context()["correlation_id"]))

    @bus.app_service(CommandConfirm)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirm) -> None:
            if mode == "mapping":
                self.context["correlation_id"] = "renamed"
            elif mode == "set":
                use_context().set("correlation_id", "renamed")
            if mode == "scope":
                with use_context().scoped({"correlation_id": "renamed"}):
                    self.feature_bus.execute(CommandCheck())
            self.feature_bus.execute(CommandCheck())

    with bus.context({"correlation_id": "from-the-entrance"}):
        bus(CommandConfirm())
    return seen


@pytest.mark.parametrize("mode", ["mapping", "set"])
def test_a_write_of_the_correlation_renames_the_flow_for_what_follows(mode: str):
    assert _flow_of(mode) == [("renamed", "renamed")]


def test_a_scope_names_the_flow_for_its_block_only():
    assert _flow_of("scope") == [
        ("renamed", "renamed"),
        ("from-the-entrance", "from-the-entrance"),
    ]


def test_a_block_can_run_with_only_what_a_parent_level_says():
    seen: list[Context] = []
    bus = UseFramework("context-parent-scope", log_after_execution=False)

    @bus.feature(CommandCheck)
    class Check(Feature):
        def execute(self, dto: CommandCheck) -> None:
            seen.append(use_context())

    @bus.app_service(CommandConfirm)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirm) -> None:
            use_context().set("pos_id", 3)
            self.feature_bus.execute(CommandCheck())
            parent = use_context().parent
            assert parent is not None
            with parent.scoped():
                self.feature_bus.execute(CommandCheck())
            entrypoint = use_context(Level.ENTRYPOINT)
            assert entrypoint is not None
            with entrypoint.scoped({"lang": "en"}):
                self.feature_bus.execute(CommandCheck())

    with bus.context({"tenant_id": "acme"}):
        bus(CommandConfirm())

    as_set, only_the_parent, only_the_entrance = seen
    assert as_set["pos_id"] == 3
    assert "pos_id" not in only_the_parent and only_the_parent["tenant_id"] == "acme"
    assert "pos_id" not in only_the_entrance and only_the_entrance["lang"] == "en"
    for context in (only_the_parent, only_the_entrance):
        assert context.execution is not None
        assert context.execution.causation_id == as_set.execution.causation_id  # type: ignore[union-attr]
