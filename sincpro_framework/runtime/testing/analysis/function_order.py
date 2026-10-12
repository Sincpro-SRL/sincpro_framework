"""Put the functions of a file in dependency order: what is used above, what uses it below.

Context: a file reads top to bottom as building blocks first and the thing that uses them last, so
a reader meets every name already explained. `in_dependency_order` answers a source with that
order; a source that comes back unchanged is already right.

Only the function that is USED moves, up, to just before the first function that uses it — nothing
is moved down, so what runs when the module loads keeps its order. It is left where it is when:

- the two call each other, directly or through others: no order satisfies both;
- its decorator does something when it is defined (registers a route, a handler): moving it
  would change the order of what is registered;
- what it needs while it is DEFINED — a decorator, a default, an annotation — is declared between
  the two.

Dunder methods stay where they are, and a name defined more than once (an overload) never moves.
Nothing here imports what it reads.
"""

import ast

SAFE_DECORATORS = frozenset(
    {
        "staticmethod",
        "classmethod",
        "property",
        "abstractmethod",
        "cached_property",
        "contextmanager",
        "asynccontextmanager",
        "final",
        "override",
        "lru_cache",
        "cache",
        "wraps",
        "overload",
    }
)
ACCESSORS = ("setter", "getter", "deleter")

type Function = ast.FunctionDef | ast.AsyncFunctionDef


class Item:
    """One statement of a body, with the comment lines that sit right above it."""

    def __init__(self, statement: ast.stmt, start: int, end: int) -> None:
        self.statement = statement
        self.start = start
        self.end = end


def _decorator_names(function: Function) -> set[str]:
    names = set()
    for decorator in function.decorator_list:
        node = decorator.func if isinstance(decorator, ast.Call) else decorator
        names.add(node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "?"))
    return names


def _registers_when_defined(function: Function) -> bool:
    return any(
        name not in SAFE_DECORATORS and not name.endswith(ACCESSORS)
        for name in _decorator_names(function)
    )


def _defined_names(statement: ast.stmt) -> set[str]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    names = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


def _needed_when_defined(function: Function, annotated_lazily: bool) -> set[str]:
    parts: list[ast.expr] = [
        *function.decorator_list,
        *function.args.defaults,
        *(default for default in function.args.kw_defaults if default),
    ]
    if not annotated_lazily:
        arguments = [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
            *(one for one in (function.args.vararg, function.args.kwarg) if one),
        ]
        parts += [one.annotation for one in arguments if one.annotation]
        if function.returns:
            parts.append(function.returns)
    return {
        node.id for part in parts for node in ast.walk(part) if isinstance(node, ast.Name)
    }


def _uses(function: Function, names: set[str], inside_a_class: bool) -> set[str]:
    """The functions of the same body that this one refers to."""
    found = set()
    for node in ast.walk(function):
        if not inside_a_class and isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
        if (
            inside_a_class
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
            and node.attr in names
        ):
            found.add(node.attr)
    return found


def _items(lines: list[str], body: list[ast.stmt]) -> list[Item]:
    items = []
    for statement in body:
        decorators = getattr(statement, "decorator_list", None)
        start = (decorators[0].lineno if decorators else statement.lineno) - 1
        while (
            start > 0
            and lines[start - 1].lstrip().startswith("#")
            and len(lines[start - 1]) - len(lines[start - 1].lstrip()) == statement.col_offset
        ):
            start -= 1
        items.append(Item(statement, start, statement.end_lineno or statement.lineno))
    return items


def _reordered_body(
    lines: list[str], body: list[ast.stmt], inside_a_class: bool, annotated_lazily: bool
) -> tuple[int, int, list[str]] | None:
    items = _items(lines, body)
    counted: dict[str, int] = {}
    for item in items:
        if isinstance(item.statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counted[item.statement.name] = counted.get(item.statement.name, 0) + 1
    movable = {
        name
        for name, count in counted.items()
        if count == 1 and not (name.startswith("__") and name.endswith("__"))
    }
    functions = {
        item.statement.name: item
        for item in items
        if isinstance(item.statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.statement.name in movable
    }
    graph = {
        name: _uses(item.statement, set(functions), inside_a_class) - {name}
        for name, item in functions.items()
        if isinstance(item.statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def reaches(origin: str, target: str, seen: set[str]) -> bool:
        for step in graph.get(origin, ()):
            if step == target:
                return True
            if step not in seen:
                seen.add(step)
                if reaches(step, target, seen):
                    return True
        return False

    order = list(items)
    moved = False
    for _ in range(2000):
        position = {id(item): index for index, item in enumerate(order)}
        settled = True
        for index, caller in enumerate(order):
            statement = caller.statement
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if statement.name not in graph:
                continue
            for name in sorted(
                graph[statement.name], key=lambda one: position[id(functions[one])]
            ):
                used = functions[name]
                target = position[id(used)]
                if target <= index or reaches(name, statement.name, set()):
                    continue
                if not isinstance(used.statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _registers_when_defined(used.statement):
                    continue
                between = set()
                for one in order[index:target]:
                    between |= _defined_names(one.statement)
                if _needed_when_defined(used.statement, annotated_lazily) & between:
                    continue
                order.pop(target)
                order.insert(index, used)
                moved = True
                settled = False
                break
            if not settled:
                break
        if settled:
            break
    if not moved:
        return None

    text: list[str] = []
    cursor = items[0].start
    for slot, original in enumerate(items):
        text.extend(lines[cursor : original.start])
        text.extend(lines[order[slot].start : order[slot].end])
        cursor = original.end
    return items[0].start, cursor, text


def _one_pass(source: str) -> str:
    lines = source.split("\n")
    tree = ast.parse(source)
    annotated_lazily = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    edits: list[tuple[int, int, list[str]]] = []

    def visit(body: list[ast.stmt], inside_a_class: bool) -> None:
        edit = _reordered_body(lines, body, inside_a_class, annotated_lazily)
        if edit:
            edits.append(edit)
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                visit(statement.body, True)

    visit(tree.body, False)
    innermost = [
        edit
        for edit in edits
        if not any(
            other is not edit and edit[0] <= other[0] and other[1] <= edit[1]
            for other in edits
        )
    ]
    for first, last, text in sorted(innermost, key=lambda edit: -edit[0]):
        lines[first:last] = text
    return "\n".join(lines)


def in_dependency_order(source: str) -> str:
    """The source with every function above the first one that uses it, where an order exists.

    >>> in_dependency_order("def use():\\n    return base()\\n\\n\\ndef base():\\n    return 1\\n")
    'def base():\\n    return 1\\n\\n\\ndef use():\\n    return base()\\n'
    """
    for _ in range(12):
        ordered = _one_pass(source)
        if ordered == source:
            break
        source = ordered
    return source
