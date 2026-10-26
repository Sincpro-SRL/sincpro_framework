"""Where per-bus observability state lives — outside setup, outside the buses.

Setup functions build things (a TracerProvider, a GlitchTip client); they do not
own them. Holding the state here is what makes a second ``build_root_bus()`` reuse
what the first one created, and what lets a test reset everything with one call.

    observability_registry.register_tracer_provider("billing", provider)
    observability_registry.tracer_provider("billing")  ->  provider   # reused by the next build
    observability_registry.tracer_provider(PROCESS)  ->  the process's own, when set here
    observability_registry.reset()                         # a test starts from nothing
"""

from typing import Any, Dict, Optional

from sincpro_framework.observability.domain import ObservabilityIdentity

PROCESS = "<process>"
"""Reserved key: the provider that owns the process, not any bounded context."""


class ObservabilityRegistry:
    """Tracer providers and error clients, keyed by the bus that owns them."""

    def __init__(self) -> None:
        """Empty: no provider, no client, no process identity announced."""
        self._tracer_providers: Dict[str, Any] = {}
        self._error_clients: Dict[str, Any] = {}
        self._process_identity: Optional[ObservabilityIdentity] = None

    def register_process_identity(self, identity: ObservabilityIdentity) -> None:
        """Announce who the process is — done when the process's tracer provider is set up, so
        `process.identity` reads the same artifact and version as the buses."""
        self._process_identity = identity

    def process_identity(self) -> Optional[ObservabilityIdentity]:
        """Identity the process announced, so transport and buses agree on it."""
        return self._process_identity

    def register_tracer_provider(self, bus: str, provider: Any) -> None:
        """Store the provider built for `bus` — or for the process, under `PROCESS`."""
        self._tracer_providers[bus] = provider

    def tracer_provider(self, bus: str) -> Any | None:
        """The provider built for `bus` (or `PROCESS`) — `None` when none was."""
        return self._tracer_providers.get(bus)

    def owns_tracer_provider(self, provider: Any) -> bool:
        """True when ``provider`` is one this framework built for some bus."""
        return any(owned is provider for owned in self._tracer_providers.values())

    def register_error_client(self, release: str, client: Any) -> None:
        """Store the GlitchTip client built for `release` — keyed by release, not by bus: the
        buses of one deployment share one release, and so one client."""
        self._error_clients[release] = client

    def error_client(self, release: str) -> Any | None:
        """The client built for `release` — `None` when none was."""
        return self._error_clients.get(release)

    def reset(self) -> None:
        """Forget every provider, client and the process identity — for tests. It does not
        shut a provider down."""
        self._tracer_providers.clear()
        self._error_clients.clear()
        self._process_identity = None


observability_registry = ObservabilityRegistry()
