"""Where a name a published 3.x release exported lives now — kept importable, with a
`DeprecationWarning` that names its new home, until the next major (#334).

    from sincpro_framework.auth.domain.identity import IdentityKind     3.40's path: still works,
        DeprecationWarning: sincpro_framework.auth.domain.identity.IdentityKind moved to
        sincpro_framework.auth — import it from there
    from sincpro_framework.auth import StaticProvider                   moved to auth.server

Context: 3.41 split the plugin into one sub-context per job (#312, #327) — `auth/domain/*` became
`auth/common/domain/*`, the providers moved to `auth.server`, `RolePermissions` to `auth.grants`.
A minor release keeps what the previous ones published: the old module paths are answered by
`MovedModules`, a finder at the end of `sys.meta_path` that only ever answers the paths in
`MOVED_MODULES` — no file of them exists, so it never shadows a real module — and a name is
resolved, and warned about, only when it is read.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
import warnings
from typing import Any

AUTH = "sincpro_framework.auth"

MOVED_MODULES: dict[str, dict[str, tuple[str, str]]] = {
    f"{AUTH}.adapters.api_key_provider": {
        "ApiKey": (f"{AUTH}.server", "ApiKey"),
        "ApiKeyProvider": (f"{AUTH}.server", "ApiKeyProvider"),
        "ApiKeyStore": (f"{AUTH}.server", "ApiKeyStore"),
        "InMemoryApiKeys": (f"{AUTH}.server", "InMemoryApiKeys"),
        "digest_of": (f"{AUTH}.server", "api_key_digest"),
    },
    f"{AUTH}.adapters.role_permissions": {
        "RolePermissions": (f"{AUTH}.grants", "RolePermissions"),
    },
    f"{AUTH}.adapters.service_token_provider": {
        "HEADER": (f"{AUTH}.server.adapters.resource_server.service_tokens", "HEADER"),
        "ServiceTokenProvider": (f"{AUTH}.server", "ServiceTokenProvider"),
    },
    f"{AUTH}.adapters.static_provider": {
        "StaticProvider": (f"{AUTH}.server", "StaticProvider"),
    },
    f"{AUTH}.domain.exceptions": {
        "AuthError": (AUTH, "AuthError"),
        "PermissionDenied": (AUTH, "PermissionDenied"),
        "Unauthenticated": (AUTH, "Unauthenticated"),
    },
    f"{AUTH}.domain.identity": {
        "ANONYMOUS": (f"{AUTH}.common.domain.identity", "ANONYMOUS"),
        "Credentials": (AUTH, "Credentials"),
        "Identity": (AUTH, "Identity"),
        "IdentityKind": (AUTH, "IdentityKind"),
    },
    f"{AUTH}.domain.permissions": {
        "AccessDeclaration": (AUTH, "AccessDeclaration"),
        "AnyOf": (AUTH, "AnyOf"),
        "ByField": (AUTH, "ByField"),
        "Permission": (AUTH, "Permission"),
        "WhenDenied": (AUTH, "WhenDenied"),
    },
    f"{AUTH}.domain.provider": {
        "AuthProvider": (AUTH, "AuthProvider"),
    },
    f"{AUTH}.entrypoint.access_control": {
        "AccessControl": (AUTH, "AccessControl"),
        "AccessDescription": (AUTH, "AccessDescription"),
    },
    f"{AUTH}.entrypoint.asgi": {
        "IdentityMiddleware": (AUTH, "IdentityMiddleware"),
    },
    f"{AUTH}.entrypoint.security": {
        "SecurityContext": (f"{AUTH}.access_control.entrypoint.security", "SecurityContext"),
        "security": (AUTH, "security"),
    },
    f"{AUTH}.infrastructure.security_context": {
        "as_identity": (f"{AUTH}.common.infrastructure.security_context", "as_identity"),
        "as_system": (f"{AUTH}.common.infrastructure.security_context", "as_system"),
        "current_identity": (
            f"{AUTH}.common.infrastructure.security_context",
            "current_identity",
        ),
    },
    f"{AUTH}.services.guard": {
        "Access": (f"{AUTH}.access_control.domain.access", "Access"),
        "BusGuard": (f"{AUTH}.access_control.services.guard", "BusGuard"),
        "Declarations": (f"{AUTH}.access_control.domain.access", "Declarations"),
        "GUARD_SEQUENCE": (f"{AUTH}.access_control.services.guard", "GUARD_SEQUENCE"),
        "HookGuard": (f"{AUTH}.access_control.services.guard", "HookGuard"),
    },
}
"""Each module path 3.26–3.40 published that no longer exists: its names, and where each lives."""

MOVED_PACKAGES = frozenset(
    f"{AUTH}.{package}"
    for package in ("adapters", "domain", "entrypoint", "infrastructure", "services")
)
"""The packages those modules lived in — answered as empty packages that hold them."""

MOVED_FROM_ROOT: dict[str, str] = {
    "ApiKey": f"{AUTH}.server",
    "ApiKeyProvider": f"{AUTH}.server",
    "ApiKeyStore": f"{AUTH}.server",
    "InMemoryApiKeys": f"{AUTH}.server",
    "RolePermissions": f"{AUTH}.grants",
    "ServiceTokenProvider": f"{AUTH}.server",
    "StaticProvider": f"{AUTH}.server",
}
"""The names `sincpro_framework.auth` exported in 3.40 that its doors hold now."""


def moved_name(old_module: str, name: str, new_module: str, new_name: str) -> Any:
    """`new_module.new_name`, read for code that asked `old_module.name` — warned once per place
    that asks (the warning points at the caller's import)."""
    target = new_module if new_name == name else f"{new_module}.{new_name}"
    warnings.warn(
        f"{old_module}.{name} moved to {target} — import it from {new_module}; "
        "the old path is removed in 4.0",
        DeprecationWarning,
        stacklevel=3,
    )
    return getattr(importlib.import_module(new_module), new_name)


class _MovedModuleLoader(importlib.abc.Loader):
    """Builds the module of an old path: an empty package, or a module whose names are read
    from where they live now."""

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return types.ModuleType(spec.name)

    def exec_module(self, module: types.ModuleType) -> None:
        old = module.__name__
        names = MOVED_MODULES.get(old, {})
        module.__doc__ = "Moved in 3.41 — kept for 3.x code; removed in 4.0 (#334)."

        def __getattr__(name: str) -> Any:
            if name in names:
                return moved_name(old, name, *names[name])
            child = f"{old}.{name}"
            if child in MOVED_MODULES:
                return importlib.import_module(child)
            raise AttributeError(f"module {old!r} has no attribute {name!r}")

        def __dir__() -> list[str]:
            return sorted(names)

        setattr(module, "__getattr__", __getattr__)
        setattr(module, "__dir__", __dir__)


class MovedModules(importlib.abc.MetaPathFinder):
    """Answers only the old module paths and packages; every other import is not its own."""

    loader = _MovedModuleLoader()

    def find_spec(
        self, fullname: str, path: Any = None, target: types.ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname in MOVED_PACKAGES:
            return importlib.machinery.ModuleSpec(fullname, self.loader, is_package=True)
        if fullname in MOVED_MODULES:
            return importlib.machinery.ModuleSpec(fullname, self.loader)
        return None


def install() -> None:
    """`MovedModules` at the end of `sys.meta_path`, once — after every real finder, so a module
    that exists is always found first."""
    if not any(isinstance(one, MovedModules) for one in sys.meta_path):
        sys.meta_path.append(MovedModules())
