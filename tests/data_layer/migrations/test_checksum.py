"""A step's checksum is what the step does, not how it is laid out.

`make format` — autoflake, isort, black — rewrites generated bodies; the checksum must survive
that, and still change when the step does something else. Manifests hashed before, with `v1:`
checksums of the bytes, keep verifying.
"""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from sincpro_framework.data_layer.migrations import (
    ContextMigrations,
    InMemoryEngine,
    MigrationRefused,
    Migrations,
)
from sincpro_framework.data_layer.migrations.adapters.manifest import (
    checksum,
    matches,
    read_manifest,
    write_manifest,
)

GENERATED = '''"""create partner

Never import domain or ORM models here.
"""

import sqlalchemy as sa
from alembic import op


revision = "01a0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('partner',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('partner')
'''

FORMATTED = '''"""create partner

    Never import domain or ORM models here.
"""

# reordered by isort, wrapped by black
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text  # unused, as autoflake would remove it

revision = "01a0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partner",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("partner")  # the whole table
'''


def _body(tmp_path: Path, source: str, name: str = "step.py") -> Path:
    path = tmp_path / name
    path.write_text(source)
    return path


def test_a_formatted_body_keeps_its_checksum(tmp_path):
    generated = checksum(_body(tmp_path, GENERATED, "generated.py"))

    assert generated.startswith("v2:")
    assert checksum(_body(tmp_path, FORMATTED, "formatted.py")) == generated


@pytest.mark.parametrize(
    "edit",
    [
        ("'name'", "'full_name'"),
        ("op.drop_table('partner')", "op.drop_column('partner', 'name')"),
        ("nullable=True", "nullable=False"),
        ("import sqlalchemy as sa", "import sqlmodel as sa"),
        ("Never import", "Always import"),
    ],
    ids=["column name", "op", "argument", "import", "docstring"],
)
def test_a_body_that_does_something_else_changes_its_checksum(tmp_path, edit):
    before, after = edit
    edited = GENERATED.replace(before, after)
    assert edited != GENERATED

    assert checksum(_body(tmp_path, edited, "edited.py")) != checksum(
        _body(tmp_path, GENERATED)
    )


def test_a_python_body_that_does_not_parse_is_refused_naming_it(tmp_path):
    body = _body(tmp_path, "def upgrade(:\n")

    with pytest.raises(MigrationRefused, match="step.py"):
        checksum(body)


def test_a_body_that_is_not_python_is_hashed_as_text(tmp_path):
    body = _body(tmp_path, "up: create inbox\n", "step.txt")
    windows = _body(tmp_path, "up: create inbox\r\n", "windows.txt")

    assert checksum(body) == checksum(windows)
    assert checksum(body) != checksum(_body(tmp_path, "up: create outbox\n", "other.txt"))


def _v1(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return f"v1:{hashlib.sha256(content).hexdigest()}"


def _project(tmp_path: Path) -> Migrations:
    common = ContextMigrations("common", tmp_path / "common")
    common.store("main", InMemoryEngine())
    return Migrations([common])


def _hashed_with_v1(migrations: Migrations) -> None:
    context = migrations.contexts["common"]
    manifest = read_manifest(context.folder, "common")
    entry = manifest.stores["main"]
    entry.steps = [
        replace(step, checksum=_v1(context.folder / "main" / step.file))
        for step in entry.steps
    ]
    write_manifest(context.folder, manifest)


def test_a_manifest_hashed_with_v1_still_verifies(tmp_path):
    migrations = _project(tmp_path)
    step = migrations.revision("common", "main", "create partner")
    _hashed_with_v1(migrations)
    body = migrations.chain("common", "main").folder / step.file

    assert matches(body, _v1(body))
    assert migrations.check() == []
    assert [one.id for one in migrations.upgrade()] == [step.id]


def test_hash_rewrites_v1_checksums_as_v2_even_for_an_applied_step(tmp_path):
    migrations = _project(tmp_path)
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    _hashed_with_v1(migrations)

    assert migrations.hash() == []  # nothing changed, only the algorithm
    steps = read_manifest(tmp_path / "common", "common").stores["main"].steps
    assert all(one.checksum.startswith("v2:") for one in steps)
    assert migrations.check() == []


def test_a_v1_checksum_of_an_edited_body_fails_the_check(tmp_path):
    migrations = _project(tmp_path)
    step = migrations.revision("common", "main", "create partner")
    _hashed_with_v1(migrations)

    (migrations.chain("common", "main").folder / step.file).write_text("edited\n")

    assert any("changed since it was hashed" in one for one in migrations.check())
