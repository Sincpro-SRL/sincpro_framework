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

from sincpro_framework.runtime.testing.analysis.architecture import _read_modules
from sincpro_framework.settings import SincproConfig


@dataclass(frozen=True)
class SettingsScopeViolation:
    """A module of one context reading the settings section of another, and where.

    SettingsScopeViolation("pkg.qr.services.pay", 12, "qr", "cybersource")
        ->  pkg.qr.services.pay:12 — context 'qr' reads the section 'cybersource' of ...
    """

    module: str
    line: int
    context: str
    section: str

    def __repr__(self) -> str:
        """One line a failing assert prints: the module and line, its context, the section."""
        return (
            f"{self.module}:{self.line} — context {self.context!r} reads the section "
            f"{self.section!r} of another context"
        )


def _imported_names(tree: ast.Module) -> set[str]:
    """Every name an import binds anywhere in the module, function bodies included — the
    roots a settings reading may go through."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return names


def _rooted_in(node: ast.expr, imported: set[str]) -> bool:
    """Whether an attribute chain starts at an imported name — ``settings.qr`` does when
    ``settings`` was imported; ``self.qr`` or a local ``config.qr`` does not."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id in imported


def settings_scope_violations(
    package: str, shape: type[SincproConfig]
) -> list[SettingsScopeViolation]:
    """Readings, in ``package``, of a section of ``shape`` from a module of another context.

    Context: the first package segment is skipped — a module's context is the first later
    segment named as a section. A reading is an attribute named after another section on a
    chain rooted at an imported name.

    Example::

        def test_each_context_reads_only_its_own_settings():
            assert settings_scope_violations("sincpro_payments_sdk", PaymentsSettings) == []

    1. The sections of ``shape`` — its fields that are a settings model of their own.
    2. Every module of ``package``, read without importing it; its context is the first
       segment after the package's that names a section — a module with none is skipped.
    3. The names the module imports, anywhere in it.
    4. Each attribute naming another context's section, on a chain that starts at one of
       those names, is a violation at its line.
    Final: the violations ordered by module, then line.
    """
    sections = {name for name in shape.model_fields if shape.field_shape(name) is not None}
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
