"""The command line a project's Makefile calls: exit code 0 when it did what was asked, 1 when it
refused or found a problem — so `make` stops there."""

from pathlib import Path

from sincpro_framework.data_layer.migrations import (
    ContextMigrations,
    InMemoryEngine,
    Migrations,
    command_line,
)


def _migrations(tmp_path: Path) -> Migrations:
    common = ContextMigrations("common", tmp_path / "common")
    common.store("main", InMemoryEngine())
    return Migrations([common])


def test_revision_upgrade_and_status_from_the_command_line(tmp_path, capsys):
    migrations = _migrations(tmp_path)

    assert (
        command_line(migrations, ["revision", "common", "main", "-m", "create partner"]) == 0
    )
    assert command_line(migrations, ["status"]) == 0
    assert "common/main" in capsys.readouterr().out

    assert command_line(migrations, ["upgrade"]) == 0
    assert command_line(migrations, ["status"]) == 0
    out = capsys.readouterr().out
    assert "up to date" in out and "create partner" in out


def test_a_refusal_exits_with_one_and_says_why(tmp_path, capsys):
    migrations = _migrations(tmp_path)
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()

    assert command_line(migrations, ["downgrade", "--to", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_check_exits_with_one_when_it_finds_a_problem(tmp_path, capsys):
    migrations = _migrations(tmp_path)
    step = migrations.revision("common", "main", "create partner")
    (migrations.chain("common", "main").folder / step.file).write_text("edited\n")

    assert command_line(migrations, ["check"]) == 1
    assert "hash" in capsys.readouterr().err
    assert command_line(migrations, ["hash"]) == 0
    assert step.key in capsys.readouterr().out  # what was just approved
    assert command_line(migrations, ["check"]) == 0


def test_every_command_a_makefile_calls(tmp_path, capsys):
    common = ContextMigrations("common", tmp_path / "common")
    common.store("main", InMemoryEngine(transactional=False))
    billing = ContextMigrations("billing", tmp_path / "billing")
    billing.store("main", InMemoryEngine())
    migrations = Migrations([common, billing])
    partner = migrations.revision("common", "main", "partner")

    assert (
        command_line(
            migrations,
            [
                "revision",
                "billing",
                "main",
                "-m",
                "invoice",
                "--requires",
                partner.key,
                "--irreversible",
            ],
        )
        == 0
    )
    invoice = migrations.status().timeline[-1][0]
    assert invoice.requires == (partner.key,) and invoice.irreversible

    assert command_line(migrations, ["upgrade", "--to", partner.id]) == 0
    assert command_line(migrations, ["upgrade"]) == 0
    assert command_line(migrations, ["downgrade", "--to", partner.id]) == 1  # irreversible
    assert "irreversible" in capsys.readouterr().err

    assert command_line(migrations, ["resolve", "common", "main"]) == 0
    assert migrations.status().chains["common/main"].position.head is None
    assert (
        command_line(migrations, ["resolve", "common", "main", "--at", partner.id[:20]]) == 0
    )
    assert migrations.status().chains["common/main"].position.head == partner.id


def test_adopt_exits_with_one_and_says_why_when_it_refuses(tmp_path, capsys):
    migrations = _migrations(tmp_path)
    migrations.revision("common", "main", "baseline")

    assert command_line(migrations, ["adopt", "common", "main"]) == 1
    assert "resolve common main" in capsys.readouterr().err


def test_plan_prints_what_would_run_and_runs_nothing(tmp_path, capsys):
    migrations = _migrations(tmp_path)
    migrations.revision("common", "main", "create partner")

    assert command_line(migrations, ["upgrade", "--plan"]) == 0
    out = capsys.readouterr().out
    assert "would apply 1 step(s)" in out and "create partner" in out
    assert migrations.upgrade_plan() != []

    migrations.upgrade()
    assert command_line(migrations, ["downgrade", "--to", "base", "--plan"]) == 0
    assert "would revert 1 step(s)" in capsys.readouterr().out
    assert migrations.upgrade_plan() == []
