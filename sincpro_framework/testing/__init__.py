"""What a consumer's test suite needs from the framework: doubles for dependencies, and
checks that keep the import graph and the layers honest."""

from ..cron.clocks import ManualClock
from .architecture import ImportCycle, LayerViolation, import_cycles, layer_violations
from .dependencies import override_dependencies

__all__ = [
    "ImportCycle",
    "LayerViolation",
    "ManualClock",
    "import_cycles",
    "layer_violations",
    "override_dependencies",
]
