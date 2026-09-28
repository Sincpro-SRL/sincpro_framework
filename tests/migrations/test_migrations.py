"""`Migrations`: where the system is, and moving it forward or back as a whole.

Every case runs on `InMemoryEngine`: the store's position lives in the engine, its step bodies
are small files in the context's folder, and `applied` records what ran, in order.
"""

import json
import time
from pathlib import Path

import pytest

from sincpro_framework.migrations import (
    ChainState,
    ContextMigrations,
    InMemoryEngine,
    MigrationFailed,
    MigrationRefused,
    Migrations,
    Position,
)


def _project(tmp_path: Path, transactional: bool = True):
    engine = InMemoryEngine(transactional=transactional)
    common = ContextMigrations("common", tmp_path / "common")
    common.store("main", engine)
    billing = ContextMigrations("billing", tmp_path / "billing")
    billing.store("main", engine)
    return Migrations([common, billing]), engine


def _keys(steps) -> list[str]:
    return [f"{step.context}:{step.message}" for step in steps]


# ---------------------------------------------------------------------------------------------
# Revisions and the manifest
# ---------------------------------------------------------------------------------------------


def test_a_revision_is_scaffolded_and_recorded_in_the_contexts_manifest(tmp_path):
    migrations, _ = _project(tmp_path)

    first = migrations.revision("common", "main", "create partner")
    second = migrations.revision("common", "main", "add tax id")

    manifest = json.loads((tmp_path / "common" / "meta_migration.json").read_text())
    steps = manifest["stores"]["main"]["steps"]
    assert [one["message"] for one in steps] == ["create partner", "add tax id"]
    assert steps[1]["parent"] == first.id and second.parent == first.id
    assert (migrations.chain("common", "main").folder / second.file).exists()
    assert manifest["sum"].startswith("v1:")


def test_each_store_of_a_context_is_a_chain_of_its_own(tmp_path):
    chat = ContextMigrations("chat", tmp_path / "chat")
    chat.store("main", InMemoryEngine())
    chat.store("messages", InMemoryEngine())
    migrations = Migrations([chat])

    account = migrations.revision("chat", "main", "create account")
    inbox = migrations.revision("chat", "messages", "create inbox")

    assert inbox.parent is None and account.parent is None
    chain = migrations.chain("chat", "messages")
    assert (chain.folder / inbox.file).exists()  # a body is found from its own chain


def test_a_revision_may_require_a_step_of_another_context(tmp_path):
    migrations, _ = _project(tmp_path)
    partner = migrations.revision("common", "main", "create partner")

    invoice = migrations.revision("billing", "main", "create invoice", requires=[partner.key])

    assert invoice.requires == (partner.key,)


# ---------------------------------------------------------------------------------------------
# Forward and back, as a whole
# ---------------------------------------------------------------------------------------------


def test_upgrade_applies_every_context_in_timeline_order(tmp_path):
    migrations, engine = _project(tmp_path)
    migrations.revision("common", "main", "1")
    migrations.revision("common", "main", "2")
    migrations.revision("billing", "main", "3")
    migrations.revision("common", "main", "4")

    applied = migrations.upgrade()

    assert _keys(applied) == ["common:1", "common:2", "billing:3", "common:4"]
    assert _keys(engine.applied) == _keys(applied)
    assert {one.state for one in migrations.status().chains.values()} == {
        ChainState.UP_TO_DATE
    }


def test_upgrade_to_a_step_stops_there(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    second = migrations.revision("billing", "main", "2")
    migrations.revision("common", "main", "3")

    applied = migrations.upgrade(to=second.id)

    assert _keys(applied) == ["common:1", "billing:2"]
    assert migrations.status().chains["common/main"].state == ChainState.BEHIND


def test_downgrade_puts_the_whole_system_back_to_a_step(tmp_path):
    migrations, engine = _project(tmp_path)
    first = migrations.revision("common", "main", "1")
    migrations.revision("billing", "main", "2")
    migrations.revision("common", "main", "3")
    migrations.upgrade()

    reverted = migrations.downgrade(to=first.id)

    assert _keys(reverted) == ["common:3", "billing:2"]
    assert engine.position(migrations.chain("common", "main")) == Position(first.id)
    assert engine.position(migrations.chain("billing", "main")) == Position(None)


def test_downgrade_to_base_reverts_everything(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    migrations.revision("billing", "main", "2")
    migrations.upgrade()

    assert _keys(migrations.downgrade(to="base")) == ["billing:2", "common:1"]


def test_a_downgrade_that_would_cross_an_irreversible_step_reverts_nothing(tmp_path):
    migrations, engine = _project(tmp_path)
    first = migrations.revision("common", "main", "1")
    migrations.revision("common", "main", "drop legacy", irreversible=True)
    migrations.revision("billing", "main", "3")
    migrations.upgrade()

    with pytest.raises(MigrationRefused, match="drop legacy.*irreversible"):
        migrations.downgrade(to=first.id)

    assert engine.reverted == []


# ---------------------------------------------------------------------------------------------
# Where the system is
# ---------------------------------------------------------------------------------------------


def test_a_store_ahead_of_the_code_is_refused_naming_what_it_does_not_know(tmp_path):
    migrations, engine = _project(tmp_path)
    migrations.revision("common", "main", "1")
    migrations.upgrade()
    newer = "ffffffffffffffffffffffffffffffff"
    engine.record(migrations.chain("common", "main"), Position(newer))

    assert migrations.status().chains["common/main"].state == ChainState.AHEAD
    with pytest.raises(MigrationRefused, match=f"common/main.*{newer}.*newer release"):
        migrations.upgrade()


def test_a_store_on_a_step_the_code_never_had_is_ahead_whatever_that_steps_id(tmp_path):
    migrations, engine = _project(tmp_path)
    migrations.revision("common", "main", "1")
    older = "00000000000000000000000000000000"  # a step merged late keeps an older id
    engine.record(migrations.chain("common", "main"), Position(older))

    assert migrations.status().chains["common/main"].state == ChainState.AHEAD
    with pytest.raises(MigrationRefused, match=f"{older}.*newer release.*abandoned"):
        migrations.upgrade()


def test_the_timeline_marks_what_is_applied(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    migrations.revision("billing", "main", "2")
    migrations.upgrade()
    migrations.revision("common", "main", "3")

    status = migrations.status()

    assert [(step.message, applied) for step, applied in status.timeline] == [
        ("1", True),
        ("2", True),
        ("3", False),
    ]


# ---------------------------------------------------------------------------------------------
# Failure and resume
# ---------------------------------------------------------------------------------------------


def test_a_failing_transactional_step_stops_the_run_and_leaves_its_store_where_it_was(
    tmp_path,
):
    migrations, engine = _project(tmp_path)
    first = migrations.revision("common", "main", "1")
    broken = migrations.revision("billing", "main", "2")
    migrations.revision("common", "main", "3")
    engine.fail_on(broken.id)

    with pytest.raises(MigrationFailed, match="billing/main.*2"):
        migrations.upgrade()

    assert _keys(engine.applied) == ["common:1"]
    status = migrations.status()
    assert status.chains["billing/main"].state == ChainState.BEHIND
    assert engine.position(migrations.chain("common", "main")) == Position(first.id)


def test_a_failing_non_transactional_step_leaves_its_store_dirty_until_resolved(tmp_path):
    migrations, engine = _project(tmp_path, transactional=False)
    first = migrations.revision("common", "main", "1")
    broken = migrations.revision("common", "main", "2")
    engine.fail_on(broken.id)

    with pytest.raises(MigrationFailed):
        migrations.upgrade()

    assert migrations.status().chains["common/main"].state == ChainState.DIRTY
    with pytest.raises(MigrationRefused, match="common/main.*dirty.*resolve"):
        migrations.upgrade()

    migrations.resolve("common", "main", at=first.id)  # a human looked: step 2 did not land
    engine.fail_on(None)

    assert _keys(migrations.upgrade()) == ["common:2"]


# ---------------------------------------------------------------------------------------------
# check and hash — for CI
# ---------------------------------------------------------------------------------------------


def test_a_step_edited_after_its_revision_fails_the_check_until_it_is_hashed(tmp_path):
    migrations, _ = _project(tmp_path)
    step = migrations.revision("common", "main", "1")
    assert migrations.check() == []

    (migrations.chain("common", "main").folder / step.file).write_text("edited\n")

    assert any(
        "common/main" in problem and "hash" in problem for problem in migrations.check()
    )
    assert migrations.hash() == [step.key]
    assert migrations.check() == []


def test_line_endings_alone_do_not_change_a_checksum(tmp_path):
    migrations, _ = _project(tmp_path)
    step = migrations.revision("common", "main", "1")
    body = migrations.chain("common", "main").folder / step.file
    body.write_bytes(body.read_bytes().replace(b"\n", b"\r\n"))

    assert migrations.check() == []


def test_two_steps_with_the_same_parent_fail_the_check(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    manifest_path = tmp_path / "common" / "meta_migration.json"
    manifest = json.loads(manifest_path.read_text())
    fork = dict(manifest["stores"]["main"]["steps"][0], id="0" * 31 + "1", message="fork")
    manifest["stores"]["main"]["steps"].append(fork)  # what two merged branches leave behind
    manifest_path.write_text(json.dumps(manifest))
    migrations.hash()

    assert any(
        "common/main" in problem and "linear" in problem for problem in migrations.check()
    )


def test_a_store_in_the_manifest_that_the_code_does_not_register_fails_the_check(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    manifest_path = tmp_path / "common" / "meta_migration.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stores"]["archive"] = {"engine": "memory", "steps": []}
    manifest_path.write_text(json.dumps(manifest))

    assert any("common/archive" in problem for problem in migrations.check())


def test_a_step_is_found_by_the_start_of_its_id(tmp_path):
    migrations, _ = _project(tmp_path)
    first = migrations.revision("common", "main", "1")
    time.sleep(0.002)  # a new millisecond: the ids differ in their first twelve characters
    migrations.revision("common", "main", "2")
    migrations.upgrade()

    assert _keys(migrations.downgrade(to=first.id[:12])) == ["common:2"]


def test_a_start_shared_by_two_steps_is_refused_naming_both(tmp_path):
    migrations, _ = _project(tmp_path)
    first = migrations.revision("common", "main", "1")
    second = migrations.revision("common", "main", "2")
    migrations.upgrade()

    with pytest.raises(MigrationRefused, match=f"{first.id}.*{second.id}"):
        migrations.downgrade(to=first.id[:8])


def test_the_dirty_refusal_names_the_step_that_failed_and_the_command_to_resolve(tmp_path):
    migrations, engine = _project(tmp_path, transactional=False)
    first = migrations.revision("common", "main", "1")
    broken = migrations.revision("common", "main", "add index")
    engine.fail_on(broken.id)
    with pytest.raises(MigrationFailed):
        migrations.upgrade()

    with pytest.raises(MigrationRefused) as refusal:
        migrations.upgrade()

    message = str(refusal.value)
    assert broken.id in message and "add index" in message
    assert f"resolve common main --at" in message and first.id in message


def test_upgrade_refuses_a_body_edited_since_it_was_hashed(tmp_path):
    migrations, engine = _project(tmp_path)
    step = migrations.revision("common", "main", "1")
    (migrations.chain("common", "main").folder / step.file).write_text("edited\n")

    with pytest.raises(MigrationRefused, match="changed since it was hashed"):
        migrations.upgrade()

    assert engine.applied == []


def test_a_downgrade_to_a_step_that_is_not_applied_is_refused(tmp_path):
    migrations, engine = _project(tmp_path)
    migrations.revision("common", "main", "1")
    migrations.upgrade()
    pending = migrations.revision("billing", "main", "2")

    with pytest.raises(MigrationRefused, match=f"{pending.id}.*not applied"):
        migrations.downgrade(to=pending.id)

    assert engine.reverted == []


def test_hash_refuses_a_step_its_store_already_applied(tmp_path):
    migrations, _ = _project(tmp_path)
    step = migrations.revision("common", "main", "1")
    migrations.upgrade()
    (migrations.chain("common", "main").folder / step.file).write_text("edited\n")

    with pytest.raises(MigrationRefused, match=f"{step.key}.*already applied.*new step"):
        migrations.hash()


def test_two_contexts_on_one_folder_are_refused(tmp_path):
    first = ContextMigrations("common", tmp_path / "shared")
    second = ContextMigrations("billing", tmp_path / "shared")

    with pytest.raises(ValueError, match="common.*billing.*one folder"):
        Migrations([first, second])


def test_a_manifest_written_by_another_context_is_refused(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    manifest_path = tmp_path / "common" / "meta_migration.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["context"] = "billing"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(MigrationRefused, match="meta_migration.json.*billing.*common"):
        migrations.status()


def test_a_store_whose_steps_were_written_for_another_engine_fails_the_check(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    manifest_path = tmp_path / "common" / "meta_migration.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stores"]["main"]["engine"] = "alembic"
    manifest_path.write_text(json.dumps(manifest))

    assert any("common/main" in one and "alembic" in one for one in migrations.check())


def test_upgrade_to_a_step_brings_the_steps_it_requires(tmp_path):
    migrations, _ = _project(tmp_path)
    partner = migrations.revision("common", "main", "partner")
    invoice = migrations.revision("billing", "main", "invoice", requires=[partner.key])

    assert _keys(migrations.upgrade(to=invoice.id)) == ["common:partner", "billing:invoice"]


def test_a_downgrade_reverts_a_step_before_the_step_it_requires(tmp_path):
    migrations, _ = _project(tmp_path)
    partner = migrations.revision("common", "main", "partner")
    migrations.revision("billing", "main", "invoice", requires=[partner.key])
    migrations.upgrade()

    assert _keys(migrations.downgrade(to="base")) == ["billing:invoice", "common:partner"]


def test_a_checksum_of_an_algorithm_this_framework_does_not_know_fails_the_check(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "1")
    manifest_path = tmp_path / "common" / "meta_migration.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stores"]["main"]["steps"][0]["checksum"] = "v9:abc"
    manifest_path.write_text(json.dumps(manifest))

    assert any("algorithm this framework does not know" in one for one in migrations.check())


def test_adopt_is_refused_by_an_engine_that_cannot_tell_drift(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "baseline")

    with pytest.raises(MigrationRefused, match="resolve common main --at"):
        migrations.adopt("common", "main")


def test_adopt_is_refused_for_a_chain_without_steps(tmp_path):
    migrations, _ = _project(tmp_path)

    with pytest.raises(MigrationRefused, match="baseline"):
        migrations.adopt("common", "main")


# ---------------------------------------------------------------------------------------------
# The plan: what a command would run, before it runs
# ---------------------------------------------------------------------------------------------


def test_the_upgrade_plan_is_what_upgrade_would_run_and_nothing_runs(tmp_path):
    migrations, engine = _project(tmp_path)
    partner = migrations.revision("common", "main", "create partner")
    migrations.revision("billing", "main", "create invoice", requires=[partner.key])
    migrations.revision("common", "main", "add tax id")

    planned = migrations.upgrade_plan(to=partner.id)

    assert _keys(planned) == ["common:create partner"]
    assert engine.applied == []
    assert _keys(migrations.upgrade_plan()) == _keys(migrations.upgrade())


def test_the_downgrade_plan_is_what_downgrade_would_revert_newest_first(tmp_path):
    migrations, engine = _project(tmp_path)
    partner = migrations.revision("common", "main", "create partner")
    migrations.revision("billing", "main", "create invoice", requires=[partner.key])
    migrations.revision("common", "main", "add tax id")
    migrations.upgrade()
    ran = list(engine.applied)

    planned = migrations.downgrade_plan(to=partner.id)

    assert _keys(planned) == ["common:add tax id", "billing:create invoice"]
    assert engine.applied == ran
    assert _keys(planned) == _keys(migrations.downgrade(to=partner.id))


def test_the_plan_refuses_what_the_command_would_refuse(tmp_path):
    migrations, _ = _project(tmp_path)
    migrations.revision("common", "main", "create partner")
    migrations.revision("common", "main", "drop legacy", irreversible=True)
    migrations.upgrade()

    with pytest.raises(MigrationRefused, match="irreversible"):
        migrations.downgrade_plan(to="base")
