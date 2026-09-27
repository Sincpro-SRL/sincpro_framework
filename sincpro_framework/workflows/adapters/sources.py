"""Where workflows come from, shipped with the framework: JSON files, and a list in memory.

    FileWorkflows(Path(__file__).parent / "workflows")     one workflow per `<name>.json`
    InMemoryWorkflows([{...}, {...}])                       tests, a screen that edits them

Context: the version is a hash of what was read, so any change — an edit, a new file, a removed
one — is a new version, and asking for it costs a read of the workflows, never a parse.
A project that keeps workflows in its database implements `WorkflowSource` on its table.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sincpro_framework.workflows.domain import RawWorkflow, WorkflowSource


def _hash(parts: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()[:16]


class FileWorkflows(WorkflowSource):
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _files(self) -> list[Path]:
        return sorted(self.folder.glob("*.json")) if self.folder.exists() else []

    def load(self) -> list[RawWorkflow]:
        if not self.folder.is_dir():
            return [RawWorkflow(str(self.folder), None, "the folder does not exist")]
        workflows: list[RawWorkflow] = []
        for path in self._files():
            try:
                workflows.append(RawWorkflow(path.name, json.loads(path.read_text())))
            except json.JSONDecodeError as error:
                workflows.append(
                    RawWorkflow(path.name, None, f"line {error.lineno}: {error.msg}")
                )
            except (UnicodeDecodeError, OSError) as error:
                workflows.append(RawWorkflow(path.name, None, str(error)))
        return workflows

    def version(self) -> str:
        contents = []
        for path in self._files():
            try:
                contents.append(path.name.encode() + path.read_bytes())
            except FileNotFoundError:
                continue
        return _hash(contents)


class InMemoryWorkflows(WorkflowSource):
    def __init__(self, workflows: Sequence[Any]) -> None:
        self._workflows = list(workflows)

    def load(self) -> list[RawWorkflow]:
        return [
            RawWorkflow(f"#{position}", workflow)
            for position, workflow in enumerate(self._workflows)
        ]

    def version(self) -> str:
        return _hash(
            [json.dumps(one, sort_keys=True, default=str).encode() for one in self._workflows]
        )
