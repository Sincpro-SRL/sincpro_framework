"""Read-only locator for dependencies registered on a UseFramework instance.

framework.add_dependency("siat", SiatClient())
framework.deps.siat              ->  the SiatClient — as `self.siat` inside a Feature
framework.deps["siat"]           ->  the same
"siat" in framework.deps         ->  True
framework.deps.missing           ->  DependencyNotRegistered, naming what is registered
framework.deps.siat = other      ->  AttributeError: register with add_dependency()
"""

from typing import Any, Iterator

from typing_extensions import TypeVar

from sincpro_framework import exceptions

TDeps = TypeVar("TDeps", default=Any)


class DependencyLocator:
    """Attribute / item view over the framework dependency registry.

    Runtime object behind ``UseFramework.deps``. Type checkers see ``TDeps``
    (typically ``DependencyContextType``) instead of this class, so
    ``framework.deps.my_adapter`` has the same autocomplete as ``self.my_adapter``
    on a Feature.
    """

    __slots__ = ("_registry",)

    def __init__(self, registry: dict[str, Any]) -> None:
        """A view over the registry itself, not a copy — a dependency added after the locator
        was handed out is seen through it. Set past the own `__setattr__`, which refuses."""
        object.__setattr__(self, "_registry", registry)

    def __getattr__(self, name: str) -> Any:
        """`deps.siat` — the dependency, or `DependencyNotRegistered` naming the others."""
        try:
            return self._registry[name]
        except KeyError:
            raise exceptions.DependencyNotRegistered(self._missing_message(name)) from None

    def __getitem__(self, name: str) -> Any:
        """`deps["siat"]` — for a name held in a variable; fails as `deps.siat` does."""
        try:
            return self._registry[name]
        except KeyError:
            raise exceptions.DependencyNotRegistered(self._missing_message(name)) from None

    def __contains__(self, name: object) -> bool:
        """`"siat" in deps` — whether a dependency is registered under that name."""
        return name in self._registry

    def __iter__(self) -> Iterator[str]:
        """The registered names, in registration order."""
        return iter(self._registry)

    def __len__(self) -> int:
        """How many dependencies are registered."""
        return len(self._registry)

    def __dir__(self) -> list[str]:
        """The registered names among the attributes, so a REPL or debugger completes them."""
        return sorted(set(super().__dir__()) | set(self._registry))

    def __repr__(self) -> str:
        """The names it holds, sorted: `DependencyLocator(db, siat)` — or `(empty)`."""
        names = ", ".join(sorted(self._registry)) or "empty"
        return f"DependencyLocator({names})"

    def __setattr__(self, name: str, value: Any) -> None:
        """Refused — a dependency is registered with `add_dependency()`, never assigned."""
        raise AttributeError(
            "Dependencies are read-only. Register them with add_dependency()."
        )

    def __delattr__(self, name: str) -> None:
        """Refused — the locator never removes what was registered."""
        raise AttributeError(
            "Dependencies are read-only. Register them with add_dependency()."
        )

    def _missing_message(self, name: str) -> str:
        """What a missing name is told: the names registered, sorted, so a typo shows."""
        available = ", ".join(sorted(self._registry)) or "(none)"
        return f"Dependency '{name}' is not registered. Available: {available}"
