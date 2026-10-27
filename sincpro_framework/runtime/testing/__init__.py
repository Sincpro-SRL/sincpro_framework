"""What a consumer's test suite needs from the framework: doubles for dependencies, a queue
that records what was published, an identity to act as, the contracts an adapter proves itself
by, and checks that verify the import graph, the layers and — for a team that wants it — each
context's settings.

    with override_dependencies(sales, partner_lookup=FakePartnerLookup()):    # a double
        sales(CommandCreateContact(name="Ana"))

    with fresh_context():                                                   # a test of its own
        billing(CommandRate(currency="USD"))

    with granting("billing.invoice.issue"):                                  # be someone
        billing(CommandIssueInvoice(invoice_id="F-1"))

    class TestRedisKeyValue(KeyValueStoreContract):                        # prove an adapter
        def make_store(self): return RedisKeyValue(fakeredis.FakeRedis())

    def test_architecture():                                                 # a CI guard
        assert import_cycles("my_sdk") == [] and layer_violations("my_sdk") == []
        assert unregistered_dependencies(sales) == {}
"""

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
from .contracts.object_store import ObjectStoreContract
from .doubles.auth import RecordingProvider, granting
from .doubles.clock import ManualClock
from .doubles.context import fresh_context
from .doubles.dependencies import override_dependencies, unregistered_dependencies
from .doubles.events import RecordingQueue

__all__ = [
    "AuthProviderContract",
    "ObjectStoreContract",
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
    "fresh_context",
    "granting",
    "import_cycles",
    "layer_violations",
    "override_dependencies",
    "settings_scope_violations",
    "unregistered_dependencies",
]
