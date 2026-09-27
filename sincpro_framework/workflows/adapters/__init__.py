"""What the framework ships behind the contracts of `domain`: Python snippets, and workflows
read from JSON files or kept in memory."""

from sincpro_framework.workflows.adapters.python_snippets import PythonSnippets
from sincpro_framework.workflows.adapters.sources import (
    FileWorkflows,
    InMemoryWorkflows,
)

__all__ = ["FileWorkflows", "InMemoryWorkflows", "PythonSnippets"]
