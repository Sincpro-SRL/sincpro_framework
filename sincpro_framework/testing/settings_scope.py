"""Read a package's source without running it, and report a context reading another's settings.

Context: with one settings singleton, `settings.cybersource.merchant_id` is one attribute away
from any module — which is the point for the global use case, and a leak for a team that wants
each context to see only its own section. This check is for that team; nothing enforces it. A
module belongs to the context whose section name first appears in its path (`apps/qr/...` is
`qr`); a module outside every section — the `settings/` package, a root entrypoint — is not
judged. A reading counts when it goes through a name the module imported.
"""

import ast
from dataclasses import dataclass

from sincpro_framework.settings.domain.config import SincproConfig
from sincpro_framework.settings.domain.resolution import nested_shape
from sincpro_framework.testing.architecture import _read_modules


@dataclass(frozen=True)
class SettingsScopeViolation:
    module: str
    line: int
    context: str
    section: str

    def __repr__(self) -> str:
        return (
            f"{self.module}:{self.line} — context {self.context!r} reads the section "
            f"{self.section!r} of another context"
        )


def _sections_of(shape: type[SincproConfig]) -> set[str]:
    return {
        name
        for name, field in shape.model_fields.items()
        if nested_shape(field.annotation) is not None
    }


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return names


def _rooted_in(node: ast.expr, imported: set[str]) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id in imported


def settings_scope_violations(
    package: str, shape: type[SincproConfig]
) -> list[SettingsScopeViolation]:
    """Readings, in ``package``, of a section of ``shape`` from a module of another context.

    Example::

        def test_each_context_reads_only_its_own_settings():
            assert settings_scope_violations("sincpro_payments_sdk", PaymentsSettings) == []
    """
    sections = _sections_of(shape)
    found: list[SettingsScopeViolation] = []
    for name, module in _read_modules(package).items():
        parts = name.split(".")[1:]
        context = next((part for part in parts if part in sections), None)
        if context is None:
            continue
        imported = _imported_names(module.tree)
        for node in ast.walk(module.tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in sections
                and node.attr != context
                and _rooted_in(node.value, imported)
            ):
                found.append(SettingsScopeViolation(name, node.lineno, context, node.attr))
    return sorted(found, key=lambda one: (one.module, one.line))
