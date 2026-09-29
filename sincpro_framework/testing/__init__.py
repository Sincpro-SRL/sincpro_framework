"""What a consumer's test suite needs from the framework: doubles for dependencies, a queue
that keeps what was published, an identity to act as, the contracts an adapter proves itself
by, and checks that keep the import graph, the layers and — for a team that wants it — each
context's settings honest."""

from ..auth import as_identity, as_system
from ..cron.adapters import ManualClock
from .architecture import ImportCycle, LayerViolation, import_cycles, layer_violations
from .auth import AuthProviderContract, RecordingProvider, granting
from .dependencies import override_dependencies, unregistered_dependencies
from .events import RecordingQueue
from .idempotency_records_contract import IdempotencyRecordsContract
from .key_value_contract import KeyValueStoreContract
from .settings_scope import SettingsScopeViolation, settings_scope_violations

__all__ = [
    "AuthProviderContract",
    "IdempotencyRecordsContract",
    "ImportCycle",
    "KeyValueStoreContract",
    "LayerViolation",
    "ManualClock",
    "RecordingProvider",
    "RecordingQueue",
    "SettingsScopeViolation",
    "as_identity",
    "as_system",
    "granting",
    "import_cycles",
    "layer_violations",
    "override_dependencies",
    "settings_scope_violations",
    "unregistered_dependencies",
]
