"""The examples people copy from run, exactly as written.

docs/persistence/guide.md and docs/core/interceptors.md each run block after block as one
program, and the two README examples
run on their own. Each is written to a real module and imported: a `DataTransferObject` reads
its field docstrings from the source, so code with no file behind it would not even define.
"""

import importlib.util
import re
import sys
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
GUIDE = ROOT / "docs" / "persistence" / "guide.md"
INTERCEPTORS = ROOT / "docs" / "core" / "interceptors.md"
README = ROOT / "README.md"
PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def _import_as_module(name: str, source: str, folder: Path) -> None:
    path = folder / f"{name}.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]


def _failing_line(error: Exception, path: Path) -> int:
    frames = [f for f in traceback.extract_tb(error.__traceback__) if f.filename == str(path)]
    return frames[-1].lineno or 0 if frames else 0


@pytest.mark.parametrize("page", [GUIDE, INTERCEPTORS], ids=lambda page: page.name)
def test_every_block_of_a_runnable_page_runs(page, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    blocks = PYTHON_BLOCK.findall(page.read_text())
    assert blocks, f"{page.name} has no python blocks"
    starts = []
    source = ""
    for block in blocks:
        starts.append(source.count("\n") + 1)
        source += block + "\n"

    try:
        _import_as_module(page.stem, source, tmp_path)
    except Exception as error:
        line = _failing_line(error, tmp_path / f"{page.stem}.py")
        number = max(i for i, start in enumerate(starts, 1) if start <= max(line, 1))
        pytest.fail(
            f"block {number} of {page.name} failed: {error!r}\n\n{blocks[number - 1]}"
        )


def test_the_readme_quick_start_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    quick_start = PYTHON_BLOCK.findall(README.read_text())[0]

    _import_as_module("readme_quick_start", quick_start, tmp_path)

    assert "Hello, Alice!" in capsys.readouterr().out


def test_the_readme_persistence_example_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    readme = README.read_text()
    section = readme[readme.index("## Persistence (ORM)\n\n`sincpro_framework.ddd`") :]
    example = PYTHON_BLOCK.findall(section)[0]

    _import_as_module("readme_persistence", example, tmp_path)

    assert "['Coffee']" in capsys.readouterr().out
