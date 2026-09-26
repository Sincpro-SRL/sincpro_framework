"""Read a package's imports without running it, and report cycles and layer violations.

Context: a circular import fails at import time, far from the change that introduced it, and
a layer rule broken today is tomorrow's cycle. Nothing here imports the package it reads, so
a consumer whose import has side effects (network, config) is safe to check. Only imports
that run when the module loads count: one inside a function or under ``if TYPE_CHECKING:``
is how a cycle is deliberately broken.
"""

import ast
import importlib.util
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

LAYERS = ("domain", "adapters", "services", "infrastructure", "entrypoints")
HANDLER_DECORATORS = ("feature", "app_service")


@dataclass(frozen=True)
class ImportCycle:
    modules: tuple[str, ...]

    def __repr__(self) -> str:
        return " → ".join((*self.modules, self.modules[0]))


@dataclass(frozen=True)
class LayerViolation:
    rule: str
    importer: str
    imported: str
    line: int
    message: str

    def __repr__(self) -> str:
        return f"[{self.rule}] {self.importer}:{self.line} imports {self.imported} — {self.message}"


@dataclass(frozen=True)
class _Edge:
    importer: str
    imported: str
    line: int
    names: tuple[str, ...]


@dataclass(frozen=True)
class _Module:
    name: str
    is_package: bool
    tree: ast.Module


# ---------------------------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------------------------


def _package_root(package: str) -> Path:
    top, *rest = package.split(".")
    spec = importlib.util.find_spec(top)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(f"{package!r} is not an importable package")
    root = Path(next(iter(spec.submodule_search_locations))).joinpath(*rest)
    if not root.is_dir():
        raise ModuleNotFoundError(f"{package!r} is not a package directory ({root})")
    return root


def _read_modules(package: str) -> dict[str, _Module]:
    root = _package_root(package)
    modules: dict[str, _Module] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        parts = [package, *relative.with_suffix("").parts]
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        name = ".".join(parts)
        modules[name] = _Module(name, is_package, ast.parse(path.read_text(), str(path)))
    return modules


def _is_type_checking(test: ast.expr) -> bool:
    match test:
        case ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING"):
            return True
    return False


def _nested_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    """The blocks inside ``node`` that run when the module loads; function bodies do not."""
    match node:
        case ast.If() if _is_type_checking(node.test):
            return [node.orelse]
        case ast.If() | ast.For() | ast.AsyncFor() | ast.While():
            return [node.body, node.orelse]
        case ast.With() | ast.AsyncWith() | ast.ClassDef():
            return [node.body]
        case ast.Try() | ast.TryStar():
            handlers = [handler.body for handler in node.handlers]
            return [node.body, *handlers, node.orelse, node.finalbody]
        case ast.Match():
            return [case.body for case in node.cases]
    return []


def _load_time_statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    for node in body:
        yield node
        for nested in _nested_bodies(node):
            yield from _load_time_statements(nested)


def _resolve_base(module: _Module, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    for _ in range(node.level - 1):
        package = package.rpartition(".")[0]
    return f"{package}.{node.module}" if node.module else package


def _deepest_known(name: str, modules: dict[str, _Module]) -> str | None:
    while name and name not in modules:
        name = name.rpartition(".")[0]
    return name or None


def _edges_of(module: _Module, modules: dict[str, _Module]) -> Iterator[_Edge]:
    """Context: ``from pkg.sales import services`` is an edge to the submodule; ``from
    pkg.sales import sales`` is an edge to the package ``__init__`` carrying the names taken.
    """
    for node in _load_time_statements(module.tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _deepest_known(alias.name, modules)
                if target:
                    yield _Edge(module.name, target, node.lineno, ())
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_base(module, node)
            if base not in modules:
                continue
            attributes = []
            for alias in node.names:
                submodule = f"{base}.{alias.name}"
                if submodule in modules:
                    yield _Edge(module.name, submodule, node.lineno, ())
                else:
                    attributes.append(alias.name)
            if attributes:
                yield _Edge(module.name, base, node.lineno, tuple(attributes))


def _first_binding_lines(module: _Module) -> dict[str, int]:
    bound: dict[str, int] = {}
    for node in _load_time_statements(module.tree.body):
        match node:
            case ast.Assign(targets=targets):
                names = [
                    leaf.id
                    for t in targets
                    for leaf in ast.walk(t)
                    if isinstance(leaf, ast.Name)
                ]
            case ast.AnnAssign(target=ast.Name(id=name), value=value) if value is not None:
                names = [name]
            case (
                ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.ClassDef(name=name)
            ):
                names = [name]
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                names = [alias.asname or alias.name.split(".")[0] for alias in aliases]
            case _:
                names = []
        for name in names:
            bound.setdefault(name, node.lineno)
    return bound


def _is_handler_class(node: ast.ClassDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in HANDLER_DECORATORS
        for decorator in node.decorator_list
    )


def _bus_bypasses(module: _Module) -> set[str]:
    """Context: names in a service module that run a use case without its bus — a class
    registered with ``@<bus>.feature`` / ``@<bus>.app_service``, and any module-level
    function. DTOs and constants are free, whatever they are called."""
    names: set[str] = set()
    for node in module.tree.body:
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                names.add(node.name)
            case ast.ClassDef() if _is_handler_class(node):
                names.add(node.name)
    return names


# ---------------------------------------------------------------------------------------------
# Graph walks
# ---------------------------------------------------------------------------------------------


def _reachable(start: str, targets: dict[str, set[str]], loading: str = "") -> set[str]:
    """Modules reached from ``start``, never passing through ``loading``: a package half-way
    through its own ``__init__`` is handed back as it is, not run again."""
    seen, queue = {start, loading}, deque([start])
    while queue:
        for nxt in targets[queue.popleft()] - seen:
            seen.add(nxt)
            queue.append(nxt)
    return seen - {loading}


def _cyclic_groups(targets: dict[str, set[str]]) -> list[set[str]]:
    """Groups of two or more nodes that each reach every other one."""
    reach = {node: _reachable(node, targets) - {node} for node in targets}
    groups: list[set[str]] = []
    grouped: set[str] = set()
    for node in sorted(targets):
        if node in grouped:
            continue
        group = {node} | {other for other in reach[node] if node in reach[other]}
        if len(group) > 1:
            groups.append(group)
            grouped |= group
    return groups


def _shortest_cycle(
    start: str, group: set[str], targets: dict[str, set[str]]
) -> tuple[str, ...]:
    previous: dict[str, str] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in sorted(targets[node] & group):
            if nxt == start:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return tuple(reversed(path))
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    return (start,)


class _Graph:
    def __init__(self, package: str) -> None:
        self.modules = _read_modules(package)
        self.edges = [e for m in self.modules.values() for e in _edges_of(m, self.modules)]
        self.edges_from: dict[str, list[_Edge]] = {name: [] for name in self.modules}
        self.targets: dict[str, set[str]] = {name: set() for name in self.modules}
        for edge in self.edges:
            self.edges_from[edge.importer].append(edge)
            if edge.importer != edge.imported:
                self.targets[edge.importer].add(edge.imported)

    def is_bootstrap(self, edge: _Edge) -> bool:
        """A module taking names from a package ``__init__`` that binds them before loading it.

        Context: the framework's bootstrap — the context ``__init__`` creates the bus, *then*
        imports ``services``, and every service takes the bus back. Python resolves that loop
        by order, so it is not a defect until the name is bound after the import that reaches
        the service.
        """
        package = self.modules[edge.imported]
        if not (
            edge.names and package.is_package and edge.importer.startswith(package.name + ".")
        ):
            return False
        reaching = [
            statement.line
            for statement in self.edges_from[package.name]
            if edge.importer in _reachable(statement.imported, self.targets, package.name)
        ]
        if not reaching:
            return True
        first = min(reaching)
        bound = _first_binding_lines(package)
        return all(bound.get(name, first) < first for name in edge.names)


# ---------------------------------------------------------------------------------------------
# Layer rules
# ---------------------------------------------------------------------------------------------


def _locate(module: str) -> tuple[str, str, str] | None:
    """``(context, layer, unit)`` of a module, or ``None`` when it sits outside every layer."""
    parts = module.split(".")
    for position, part in enumerate(parts):
        if part in LAYERS:
            unit = parts[position + 1] if position + 1 < len(parts) else ""
            return ".".join(parts[:position]), part, unit
    return None


def _layer_rule(edge: _Edge, graph: _Graph) -> LayerViolation | None:
    here, there = _locate(edge.importer), _locate(edge.imported)
    if here is None or there is None:
        return None
    context, layer, unit = here
    other_context, other_layer, other_unit = there

    def violation(rule: str, message: str) -> LayerViolation:
        return LayerViolation(rule, edge.importer, edge.imported, edge.line, message)

    if other_layer == "entrypoints" and layer != "entrypoints":
        return violation(
            "entrypoints-are-outermost",
            "entrypoints expose the bus to another process; nothing inside a context calls them",
        )
    if other_layer == "services" and edge.names:
        bypasses = _bus_bypasses(graph.modules[edge.imported])
        taken = [name for name in edge.names if name in bypasses]
        if taken and edge.imported.startswith(edge.importer + "."):
            return violation(
                "services-reused-through-bus",
                f"re-exports {', '.join(taken)}; `from . import <module>` is enough to "
                "register it, and callers reach it through its DTO on the bus",
            )
        if taken:
            return violation(
                "services-reused-through-bus",
                f"takes {', '.join(taken)} from another use case; execute that use case's "
                "DTO on the bus instead — calling it directly skips its span and error report",
            )
    if context != other_context:
        return None
    if layer == "domain" and other_layer != "domain":
        return violation(
            "domain-is-vocabulary",
            f"domain/ reaches {other_layer}/; declare a Protocol in domain/ and let the "
            "Feature receive the implementation",
        )
    if layer == "adapters" and other_layer == "adapters" and unit != other_unit:
        return violation(
            "adapters-are-independent",
            "an adapter never imports another adapter; composing two is a Feature's job",
        )
    return None


def _context_cycles(graph: _Graph) -> Iterator[LayerViolation]:
    examples: dict[tuple[str, str], _Edge] = {}
    for edge in graph.edges:
        here, there = _locate(edge.importer), _locate(edge.imported)
        if here and there and here[0] != there[0]:
            examples.setdefault((here[0], there[0]), edge)

    targets: dict[str, set[str]] = {context: set() for pair in examples for context in pair}
    for importer_context, imported_context in examples:
        targets[importer_context].add(imported_context)

    for group in _cyclic_groups(targets):
        cycle = _shortest_cycle(min(group), group, targets)
        described = " → ".join((*cycle, cycle[0]))
        for position, context in enumerate(cycle):
            edge = examples[(context, cycle[(position + 1) % len(cycle)])]
            yield LayerViolation(
                "contexts-are-acyclic",
                edge.importer,
                edge.imported,
                edge.line,
                f"contexts depend on each other ({described}); one of them must stop "
                "reaching the other — move the shared piece down, or pass it in the Command",
            )


def _common_reaching_up(graph: _Graph) -> Iterator[LayerViolation]:
    for edge in graph.edges:
        here, there = _locate(edge.importer), _locate(edge.imported)
        if not (here and there) or here[0].rpartition(".")[2] != "common":
            continue
        if here[0] != there[0] and not here[0].startswith(there[0] + "."):
            yield LayerViolation(
                "common-imports-no-context",
                edge.importer,
                edge.imported,
                edge.line,
                "common/ is the foundation every context stands on; it never reaches back up",
            )


# ---------------------------------------------------------------------------------------------
# Public checks
# ---------------------------------------------------------------------------------------------


def import_cycles(package: str) -> list[ImportCycle]:
    """Every group of modules in ``package`` that import each other when they load.

    Context: one cycle per group — the shortest through its first module; breaking it may
    reveal another, which the next run reports. The framework's bootstrap (bus first, then
    ``services``) is not a cycle while the bus is bound before that import.

    Example::

        def test_no_import_cycles():
            assert import_cycles("my_sdk") == []
    """
    graph = _Graph(package)
    targets: dict[str, set[str]] = {name: set() for name in graph.modules}
    for edge in graph.edges:
        if edge.importer != edge.imported and not graph.is_bootstrap(edge):
            targets[edge.importer].add(edge.imported)
    cycles = [
        ImportCycle(_shortest_cycle(min(group), group, targets))
        for group in _cyclic_groups(targets)
    ]
    return sorted(cycles, key=lambda cycle: cycle.modules)


def layer_violations(package: str, ignore: Iterable[str] = ()) -> list[LayerViolation]:
    """Imports in ``package`` that break the framework's layering conventions.

    Context: a module belongs to the first of ``domain``, ``adapters``, ``services``,
    ``infrastructure`` or ``entrypoints`` in its path, and to the context named by the path
    before it; modules outside every layer are not judged. These are conventions, not a
    cage: pass the ``rule`` of any a project does not follow in ``ignore``.

    Rules:
        domain-is-vocabulary         domain/ imports only domain/ of its own context
        adapters-are-independent     an adapter does not import a different adapter
        services-reused-through-bus  no registered handler class or function is imported
                                     from a service module; its DTO goes on the bus
        entrypoints-are-outermost    only entrypoints import entrypoints
        contexts-are-acyclic         two contexts never depend on each other
        common-imports-no-context    a context named ``common`` imports no sibling context

    Example::

        def test_layers():
            assert layer_violations("sincpro_odoo_mcp") == []
    """
    skipped = set(ignore)
    graph = _Graph(package)
    found = [v for v in (_layer_rule(edge, graph) for edge in graph.edges) if v is not None]
    found += [*_context_cycles(graph), *_common_reaching_up(graph)]
    unique = {(v.rule, v.importer, v.imported, v.line): v for v in found}
    return sorted(
        (v for v in unique.values() if v.rule not in skipped),
        key=lambda v: (v.rule, v.importer, v.line),
    )
