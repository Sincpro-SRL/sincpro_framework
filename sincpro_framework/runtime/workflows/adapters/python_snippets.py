"""`PythonSnippets`: snippets are Python, run in this process — the engine the framework ships.

    return {"total": steps["order"]["total"] * 9 // 10}

Context: a snippet is the body of a function whose parameters are the names it is given —
`input`, `steps` and, in a `for_each`, `item` — so `return` is how it answers. It is checked with
`ast` when it is loaded — syntax, a `return`, and every name it reads being one it has or a
builtin — never with an opcode list, which breaks on the next Python. It is compiled under its
own filename, and its source is registered in `linecache`, so a traceback shows its lines.

Open: it can do anything Python in this process can. It cannot be stopped once it runs.
"""

import ast
import builtins
import linecache
from collections.abc import Mapping, Sequence
from typing import Any

from sincpro_framework.runtime.workflows.domain.snippets import SnippetEngine

FUNCTION = "snippet"


def _parse(source: str) -> ast.Module:
    return ast.parse(source, mode="exec")


def _assigned(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _own(tree: ast.Module) -> list[ast.AST]:
    """The snippet's own nodes — not those of a function or a class it defines, whose `return`
    and `yield` are theirs."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    found: list[ast.AST] = []
    waiting: list[ast.AST] = list(tree.body)
    while waiting:
        node = waiting.pop()
        found.append(node)
        if not isinstance(node, nested):
            waiting.extend(ast.iter_child_nodes(node))
    return found


def _read(tree: ast.Module) -> list[ast.Name]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    ]


def _as_function(tree: ast.Module, names: Sequence[str]) -> ast.Module:
    """The snippet's statements as the body of `snippet(<names>)` — their line numbers kept, so
    a traceback points at the snippet's own lines."""
    arguments = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=name) for name in names],
        kwonlyargs=[],
        kw_defaults=[],
        defaults=[],
    )
    function = ast.FunctionDef(
        name=FUNCTION,
        args=arguments,
        body=tree.body or [ast.Pass()],
        decorator_list=[],
        returns=None,
        type_params=[],
    )
    module = ast.Module(body=[function], type_ignores=[])
    return ast.fix_missing_locations(module)


class PythonSnippets(SnippetEngine):
    def check(self, source: str, names: Sequence[str]) -> list[str]:
        try:
            tree = _parse(source)
        except SyntaxError as error:
            return [f"line {error.lineno}: {error.msg}"]
        problems: list[str] = []
        own = _own(tree)
        if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in own):
            problems.append("it yields: a snippet answers once, with `return {...}`")
        if not any(isinstance(node, ast.Return) for node in own):
            problems.append("it never returns: a snippet answers with `return {...}`")
        known = set(names) | _assigned(tree) | set(dir(builtins))
        for node in _read(tree):
            if node.id not in known:
                problems.append(
                    f"line {node.lineno}: it reads {node.id}, which it does not have — it has "
                    f"{', '.join(names)}"
                )
                known.add(node.id)
        return problems

    def run(self, source: str, filename: str, values: Mapping[str, Any]) -> Any:
        names = list(values)
        code = compile(_as_function(_parse(source), names), filename, "exec")
        lines = source.splitlines(keepends=True)
        linecache.cache[filename] = (len(source), None, lines, filename)
        namespace: dict[str, Any] = {}
        exec(code, namespace)
        return namespace[FUNCTION](**values)
