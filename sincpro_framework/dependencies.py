"""Read-only locator for dependencies registered on a UseFramework instance."""

from typing import Any, Iterator

from typing_extensions import TypeVar

from .exceptions import DependencyNotRegistered

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
        object.__setattr__(self, "_registry", registry)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._registry[name]
        except KeyError:
            raise DependencyNotRegistered(self._missing_message(name)) from None

    def __getitem__(self, name: str) -> Any:
        try:
            return self._registry[name]
        except KeyError:
            raise DependencyNotRegistered(self._missing_message(name)) from None

    def __contains__(self, name: object) -> bool:
        return name in self._registry

    def __iter__(self) -> Iterator[str]:
        return iter(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(self._registry))

    def __repr__(self) -> str:
        names = ", ".join(sorted(self._registry)) or "empty"
        return f"DependencyLocator({names})"

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            "Dependencies are read-only. Register them with add_dependency()."
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            "Dependencies are read-only. Register them with add_dependency()."
        )

    def _missing_message(self, name: str) -> str:
        available = ", ".join(sorted(self._registry)) or "(none)"
        return f"Dependency '{name}' is not registered. Available: {available}"
