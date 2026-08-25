from typing import assert_type

from sincpro_framework import UseFramework


class TokenAdapter:
    def generate(self) -> str:
        return "tok"


class DependencyContextType:
    token_adapter: TokenAdapter


def verify_typed_deps_from_root() -> None:
    framework = UseFramework[DependencyContextType]("typed-deps")
    framework.add_dependency("token_adapter", TokenAdapter())

    adapter = framework.deps.token_adapter
    assert_type(adapter, TokenAdapter)
    assert_type(adapter.generate(), str)
    assert_type(framework, UseFramework[DependencyContextType])
