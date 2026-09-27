"""`WorkflowSource`: where workflows come from — files in the repository, rows a tenant edits.

Context: a source answers raw workflows — whatever was stored, valid or not — and a version
that changes whenever any of them does. Validation is not the source's job: the set is checked
as a whole, against the bus it will run on, before it replaces the one in force.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sincpro_framework.workflows.domain.workflow import Workflow


@dataclass(frozen=True)
class RawWorkflow:
    origin: str
    """Where it came from — a file, a row — for the issues that name it."""

    content: Any
    error: str = ""
    """Why it could not be read at all, when it could not."""


class WorkflowSource(ABC):
    @abstractmethod
    def load(self) -> list[RawWorkflow]: ...

    @abstractmethod
    def version(self) -> str:
        """Changes whenever any definition does — cheap to ask, asked often."""


@dataclass(frozen=True)
class WorkflowSet:
    """One version of every workflow of a bounded context, validated as a whole — never changed,
    only replaced."""

    workflows: Mapping[str, Workflow]
    version: str
