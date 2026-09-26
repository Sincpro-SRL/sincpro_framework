"""docs/persistence/guide.md runs, block after block, as one program.

The guide is the page people copy from, so every example on it is executed here: a change
that breaks one fails this test instead of the next reader's first attempt.
"""

import re
import sys
import types
from pathlib import Path

import pytest

GUIDE = Path(__file__).parents[2] / "docs" / "persistence" / "guide.md"
PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def test_every_block_of_the_persistence_guide_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    guide = types.ModuleType("persistence_guide")
    monkeypatch.setitem(sys.modules, guide.__name__, guide)

    blocks = PYTHON_BLOCK.findall(GUIDE.read_text())
    assert blocks, "the guide has no python blocks"

    for number, source in enumerate(blocks, 1):
        try:
            exec(compile(source, f"{GUIDE.name} block {number}", "exec"), guide.__dict__)
        except Exception as error:
            pytest.fail(f"block {number} of {GUIDE.name} failed: {error!r}\n\n{source}")


def test_the_readme_quick_start_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    readme = GUIDE.parents[2] / "README.md"
    quick_start = PYTHON_BLOCK.findall(readme.read_text())[0]

    exec(compile(quick_start, "README.md quick start", "exec"), {"__name__": "quick_start"})

    assert "Hello, Alice!" in capsys.readouterr().out
