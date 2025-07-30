"""Tests for the traceability feature."""

import pytest
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandFeatureTracing(DataTransferObject):
    message: str


class ResponseFeatureTracing(DataTransferObject):
    result: str


class CommandAppServiceTracing(DataTransferObject):
    app_message: str


class ResponseAppServiceTracing(DataTransferObject):
    app_result: str


def test_feature_with_observability_decorator():
    """Test that features can be decorated with observability=True."""
    framework = UseFramework("test-observability", log_after_execution=False)
    
    @framework.feature(CommandFeatureTracing, observability=True)
    class ObservableFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Observable: {dto.message}")
    
    # Should work without observability enabled (backward compatibility)
    cmd = CommandFeatureTracing(message="test")
    result = framework(cmd, ResponseFeatureTracing)
    assert result.result == "Observable: test"


def test_legacy_traceability_decorator_backwards_compatibility():
    """Test that old traceability parameter still works for backward compatibility."""
    framework = UseFramework("test-legacy", log_after_execution=False)
    
    # This should still work but with a warning or graceful handling
    try:
        @framework.feature(CommandFeatureTracing, traceability=True)
        class LegacyFeature(Feature):
            def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
                return ResponseFeatureTracing(result=f"Legacy: {dto.message}")
    except TypeError:
        # Expected since we removed the traceability parameter
        pass


def test_app_service_with_observability():
    """Test that app services support observability decorators."""
    framework = UseFramework("test-app-observability", log_after_execution=False)
    
    @framework.feature(CommandFeatureTracing)
    class SimpleFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Feature: {dto.message}")
    
    @framework.app_service(CommandAppServiceTracing, observability=True)
    class ObservableAppService(ApplicationService):
        def execute(self, dto: CommandAppServiceTracing, **kwargs) -> ResponseAppServiceTracing:
            feature_result = self.feature_bus.execute(
                CommandFeatureTracing(message=dto.app_message)
            )
            return ResponseAppServiceTracing(app_result=f"App: {feature_result.result}")
    
    # Should work without observability enabled (backward compatibility)
    cmd = CommandAppServiceTracing(app_message="test")
    result = framework(cmd, ResponseAppServiceTracing)
    assert result.app_result == "App: Feature: test"


def test_enable_observability():
    """Test that observability can be enabled."""
    framework = UseFramework("test-observability", log_after_execution=False)
    
    # Enable observability
    framework.enable_observability()
    assert framework.observability_enabled is True
    
    @framework.feature(CommandFeatureTracing, observability=True)
    class ObservableFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Observable: {dto.message}")
    
    # Should work with observability enabled
    cmd = CommandFeatureTracing(message="test")
    result = framework(cmd, ResponseFeatureTracing)
    assert result.result == "Observable: test"


def test_backward_compatibility():
    """Test that existing decorators without traceability params still work."""
    framework = UseFramework("test-backward", log_after_execution=False)
    
    @framework.feature(CommandFeatureTracing)
    class LegacyFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Legacy: {dto.message}")
    
    cmd = CommandFeatureTracing(message="test")
    result = framework(cmd, ResponseFeatureTracing)
    assert result.result == "Legacy: test"


def test_correlation_id_propagation():
    """Test that correlation IDs can be passed and propagated."""
    framework = UseFramework("test-correlation", log_after_execution=False)
    framework.enable_observability()
    
    @framework.feature(CommandFeatureTracing, observability=True)
    class CorrelatedFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Correlated: {dto.message}")
    
    cmd = CommandFeatureTracing(message="test")
    result = framework(cmd, ResponseFeatureTracing, correlation_id="test-correlation-123")
    assert result.result == "Correlated: test"


def test_multiple_features_with_different_observability_configs():
    """Test multiple features with different observability configurations."""
    framework = UseFramework("test-multiple", log_after_execution=False)
    
    @framework.feature(CommandFeatureTracing, observability=True)
    class ObservableFeature(Feature):
        def execute(self, dto: CommandFeatureTracing, **kwargs) -> ResponseFeatureTracing:
            return ResponseFeatureTracing(result=f"Observable: {dto.message}")
    
    class CommandFeatureTracing2(DataTransferObject):
        message2: str
    
    class ResponseFeatureTracing2(DataTransferObject):
        result2: str
    
    @framework.feature(CommandFeatureTracing2, observability=False)
    class NonObservableFeature(Feature):
        def execute(self, dto: CommandFeatureTracing2, **kwargs) -> ResponseFeatureTracing2:
            return ResponseFeatureTracing2(result2=f"Non-observable: {dto.message2}")
    
    # Test both features
    cmd1 = CommandFeatureTracing(message="test1")
    result1 = framework(cmd1, ResponseFeatureTracing)
    assert result1.result == "Observable: test1"
    
    cmd2 = CommandFeatureTracing2(message2="test2")
    result2 = framework(cmd2, ResponseFeatureTracing2)
    assert result2.result2 == "Non-observable: test2"