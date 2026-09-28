"""What a consumer's test suite needs from the framework: doubles for dependencies, a queue
that keeps what was published, and checks that keep the import graph, the layers and — for a
team that wants it — each context's settings honest."""

from ..cron.adapters import ManualClock
from .architecture import ImportCycle, LayerViolation, import_cycles, layer_violations
from .dependencies import override_dependencies, unregistered_dependencies
from .events import RecordingQueue
from .key_value_contract import KeyValueStoreContract
from .settings_scope import SettingsScopeViolation, settings_scope_violations

__all__ = [
    "ImportCycle",
    "KeyValueStoreContract",
    "LayerViolation",
    "ManualClock",
    "RecordingQueue",
    "SettingsScopeViolation",
    "import_cycles",
    "layer_violations",
    "override_dependencies",
    "settings_scope_violations",
    "unregistered_dependencies",
]
