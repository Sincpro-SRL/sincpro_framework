"""Where per-bus observability state lives — outside setup, outside the buses.

Setup functions build things (a TracerProvider, a GlitchTip client); they do not
own them. Keeping the state here is what makes a second ``build_root_bus()`` reuse
what the first one created, and what lets a test reset everything with one call.
"""

from typing import Any, Dict, Optional

from sincpro_framework.observability.domain import ObservabilityIdentity

PROCESS = "<process>"
"""Reserved key: the provider that owns the process, not any bounded context."""


class ObservabilityRegistry:
    """Tracer providers and error clients, keyed by the bus that owns them."""

    def __init__(self) -> None:
        self._tracer_providers: Dict[str, Any] = {}
        self._error_clients: Dict[str, Any] = {}
        self._process_identity: Optional[ObservabilityIdentity] = None

    def register_process_identity(self, identity: ObservabilityIdentity) -> None:
        self._process_identity = identity

    def process_identity(self) -> Optional[ObservabilityIdentity]:
        """Identity the process announced, so transport and buses agree on it."""
        return self._process_identity

    def register_tracer_provider(self, bus: str, provider: Any) -> None:
        self._tracer_providers[bus] = provider

    def tracer_provider(self, bus: str) -> Any | None:
        return self._tracer_providers.get(bus)

    def owns_tracer_provider(self, provider: Any) -> bool:
        """True when ``provider`` is one this framework built for some bus."""
        return any(owned is provider for owned in self._tracer_providers.values())

    def register_error_client(self, release: str, client: Any) -> None:
        self._error_clients[release] = client

    def error_client(self, release: str) -> Any | None:
        return self._error_clients.get(release)

    def reset(self) -> None:
        self._tracer_providers.clear()
        self._error_clients.clear()
        self._process_identity = None


registry = ObservabilityRegistry()
