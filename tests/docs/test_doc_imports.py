"""Every `from sincpro_framework… import …` a reader copies from the docs resolves.

The README, docs/ and the shipped skills are where a project learns an import path; a module
moved without them is a project that breaks reading the docs. PRDs are history and keep the
paths of their time.
"""

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
IMPORT = re.compile(r"^\s*from (sincpro_framework[\w.]*) import \(?([\w, ]+)", re.M)
SOURCES = [
    ROOT / "README.md",
    *(page for page in (ROOT / "docs").rglob("*.md") if "prd" not in page.parts),
    *(ROOT / ".claude" / "skills").rglob("*.md"),
]


def _imports() -> list[tuple[str, str, str]]:
    """(page, module, name) for every name a page imports from the framework."""
    found = []
    for page in SOURCES:
        for match in IMPORT.finditer(page.read_text()):
            for name in match.group(2).split(","):
                if name.strip():
                    found.append(
                        (
                            str(page.relative_to(ROOT)),
                            match.group(1),
                            name.split(" as ")[0].strip(),
                        )
                    )
    return found


def _resolves(module: str, name: str) -> bool:
    try:
        imported = importlib.import_module(module)
    except ImportError:
        return False
    if hasattr(imported, name):
        return True
    try:
        importlib.import_module(f"{module}.{name}")
    except ImportError:
        return False
    return True


def test_every_documented_import_resolves() -> None:
    imports = _imports()
    assert imports, "the scan found no import: the pattern or the pages moved"
    broken = sorted(
        {
            f"{page}: from {module} import {name}"
            for page, module, name in imports
            if not _resolves(module, name)
        }
    )
    assert not broken, "imports the docs teach that do not resolve:\n" + "\n".join(broken)
