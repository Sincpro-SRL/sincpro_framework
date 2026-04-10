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
