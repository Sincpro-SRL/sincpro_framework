"""What a consumer's test suite needs from the framework: doubles for dependencies, a queue
that keeps what was published, an identity to act as, the contracts an adapter proves itself
by, and checks that keep the import graph, the layers and — for a team that wants it — each
context's settings honest."""

from sincpro_framework.entrypoints.adapters.cron.adapters import ManualClock

from .analysis.architecture import (
    ImportCycle,
    LayerViolation,
    import_cycles,
    layer_violations,
)
from .analysis.settings_scope import SettingsScopeViolation, settings_scope_violations
from .contracts.auth_provider import AuthProviderContract
from .contracts.caching_strategies import CodecContract, EvictionContract, FreshnessContract
from .contracts.idempotency_records import IdempotencyRecordsContract
from .contracts.key_value_store import KeyValueStoreContract
from .doubles.auth import RecordingProvider, granting
from .doubles.dependencies import override_dependencies, unregistered_dependencies
from .doubles.events import RecordingQueue

__all__ = [
    "AuthProviderContract",
    "CodecContract",
    "EvictionContract",
    "FreshnessContract",
    "IdempotencyRecordsContract",
    "ImportCycle",
    "KeyValueStoreContract",
    "LayerViolation",
    "ManualClock",
    "RecordingProvider",
    "RecordingQueue",
    "SettingsScopeViolation",
    "granting",
    "import_cycles",
    "layer_violations",
    "override_dependencies",
    "settings_scope_violations",
    "unregistered_dependencies",
]
