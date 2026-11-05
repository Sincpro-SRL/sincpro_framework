import shutil
import subprocess
from pathlib import Path

import pytest


class TestTypingAndLinter:
    """Typing and linter checks for static examples."""

    @staticmethod
    def _run_pyright(target: Path) -> subprocess.CompletedProcess[str]:
        """Run pyright over a file or directory using local executable or Poetry."""
        pyright_bin = shutil.which("pyright")
        if pyright_bin:
            command = [pyright_bin, str(target)]
        else:
            poetry_bin = shutil.which("poetry")
            if not poetry_bin:
                pytest.skip("pyright or poetry executable is required for this test")
            command = [poetry_bin, "run", "pyright", str(target)]

        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )

    def test_pyright_typing_cases_folder(self):
        """Run pyright against all typing examples in typing_cases."""
        typing_cases_dir = Path(__file__).resolve().parent / "typing_cases"
        assert typing_cases_dir.exists(), "typing_cases directory does not exist"

        result = self._run_pyright(typing_cases_dir)

        assert result.returncode == 0, (
            "Pyright reported typing issues in typing_cases.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_pyright_rejects_a_text_where_a_key_holds_a_whole_number(self, tmp_path: Path):
        """`EntityID[Partner, int]` is an `int` in the editor: a text there is a mistake the
        editor shows, not one the server finds."""
        case = tmp_path / "rejected_key.py"
        case.write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "from sincpro_framework import DataTransferObject\n"
            "from sincpro_framework.ddd import Entity, EntityID\n"
            "\n"
            "@dataclass(kw_only=True)\n"
            "class Partner(Entity):\n"
            "    name: str = ''\n"
            "\n"
            "class CommandBill(DataTransferObject):\n"
            "    partner: EntityID[Partner, int]\n"
            "\n"
            "CommandBill(partner='42')\n"
        )

        result = self._run_pyright(case)

        assert result.returncode != 0
        assert 'cannot be assigned to parameter "partner"' in result.stdout

    def test_pyright_rejects_a_response_type_that_is_neither(self, tmp_path: Path):
        """The bound is widened, not dropped: a scalar is still refused.

        Without this the widening could go all the way to no bound at all and nothing here
        would notice — `typing_cases` only proves what is accepted.
        """
        case = tmp_path / "rejected_response_type.py"
        case.write_text(
            "from sincpro_framework import DataTransferObject, UseFramework\n"
            "\n"
            "class Command(DataTransferObject):\n"
            "    identifier: str\n"
            "\n"
            "framework = UseFramework('rejected')\n"
            "framework(Command(identifier='x'), int)\n"
            "framework(Command(identifier='x'), str)\n"
        )

        result = self._run_pyright(case)

        assert result.returncode != 0, f"pyright accepted a scalar:\n{result.stdout}"
        assert "int" in result.stdout and "str" in result.stdout
