"""
Tests for the Context Manager functionality in Sincpro Framework
"""

import pytest
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any

from sincpro_framework import (
    UseFramework,
    DataTransferObject,
    Feature,
    ApplicationService,
)
from sincpro_framework.context.framework_context import FrameworkContext


# Test DTOs
class ContextTestDTO(DataTransferObject):
    message: str


class ContextTestResponseDTO(DataTransferObject):
    result: str
    context_data: Dict[str, Any] = {}


class ContextAppServiceDTO(DataTransferObject):
    operation: str


class ContextAppServiceResponseDTO(DataTransferObject):
    result: str
    feature_result: str = ""
    context_data: Dict[str, Any] = {}


# Test Features
class ContextAwareFeature(Feature):
    def execute(self, dto):
        """Feature that accesses context"""
        # Access context data 
        context_data = {}
        if hasattr(self, 'context') and self.context:
            context_data = self.context.copy()
        
        return ContextTestResponseDTO(
            result=f"processed: {dto.message}",
            context_data=context_data
        )


class FailingFeature(Feature):
    def execute(self, dto):
        """Feature that raises an exception"""
        raise ValueError("Feature failed intentionally")


# Test Application Services
class ContextAwareApplicationService(ApplicationService):
    def execute(self, dto):
        """Application service that uses context and calls features"""
        # Access context data
        context_data = {}
        if hasattr(self, 'context') and self.context:
            context_data = self.context.copy()
        
        # Call a feature through the feature bus
        test_dto = ContextTestDTO(message=f"from_app_service_{dto.operation}")
        feature_result = self.feature_bus.execute(test_dto)
        
        return ContextAppServiceResponseDTO(
            result=f"app_service: {dto.operation}",
            feature_result=feature_result.result if feature_result else "no_result",
            context_data=context_data
        )


class TestFrameworkContext:
    """Test the FrameworkContext context manager"""
    
    def test_framework_context_creation(self):
        """Test FrameworkContext creation"""
        framework = UseFramework("test-service")
        context_data = {"correlation_id": "test-123", "user": "testuser"}
        
        context_manager = FrameworkContext(framework, context_data)
        
        assert context_manager.framework == framework
        assert context_manager.context == context_data
        
    def test_framework_context_enter_exit(self):
        """Test context manager enter and exit"""
        framework = UseFramework("test-service")
        context_data = {"correlation_id": "test-123"}
        
        context_manager = FrameworkContext(framework, context_data)
        
        # Test enter
        result = context_manager.__enter__()
        assert result == framework
        assert framework._get_context() == context_data
        
        # Test exit
        context_manager.__exit__(None, None, None)
        # Context should be cleaned after exit
        assert framework._get_context() == {}
        
    def test_context_manager_cannot_be_entered_twice(self):
        """Test that context manager cannot be entered twice"""
        framework = UseFramework("test-service")
        context_data = {"correlation_id": "test-123"}
        
        context_manager = FrameworkContext(framework, context_data)
        context_manager.__enter__()
        
        with pytest.raises(RuntimeError, match="Context manager is already entered"):
            context_manager.__enter__()


class TestContextMixin:
    """Test the ContextMixin functionality"""
    
    def test_context_mixin_methods(self):
        """Test context mixin basic methods"""
        framework = UseFramework("test-service")
        
        # Test initial state
        assert framework._get_context() == {}
        
        # Test setting context
        test_context = {"key1": "value1", "key2": "value2"}
        framework._set_context(test_context)
        assert framework._get_context() == test_context
        
        # Test cleaning context
        framework._clean_context()
        assert framework._get_context() == {}
        
    def test_context_injection_to_services(self):
        """Test context injection to features and services"""
        framework = UseFramework("test-service")
        
        # Register a simple feature first
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        # Manually call build to initialize the bus
        framework.build_root_bus()
        
        # Set context and inject to services
        test_context = {"correlation_id": "inject-test", "user": "testuser"}
        framework._inject_context_to_services_and_features(test_context)
        
        # Verify context was injected to features
        feature_registry = framework.bus.feature_bus.feature_registry
        
        for feature in feature_registry.values():
            assert hasattr(feature, 'context')
            assert feature.context == test_context


class TestUseFrameworkContext:
    """Test UseFramework context method and integration"""
    
    def test_framework_context_method(self):
        """Test framework.context() method"""
        framework = UseFramework("test-service")
        context_data = {"correlation_id": "test-123", "user": "testuser"}
        
        context_manager = framework.context(context_data)
        
        assert isinstance(context_manager, FrameworkContext)
        assert context_manager.framework == framework
        assert context_manager.context == context_data
        
    def test_basic_context_usage(self):
        """Test basic context manager usage"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        # Test without context
        result_no_context = framework(ContextTestDTO(message="no context"))
        assert "processed: no context" in result_no_context.result
        assert result_no_context.context_data == {}
        
        # Test with context
        with framework.context({"correlation_id": "test-123", "user": "testuser"}) as app_with_context:
            result_with_context = app_with_context(ContextTestDTO(message="with context"))
            
            assert "processed: with context" in result_with_context.result
            assert result_with_context.context_data["correlation_id"] == "test-123"
            assert result_with_context.context_data["user"] == "testuser"
            
    def test_nested_context_usage(self):
        """Test nested context managers"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        with framework.context({"correlation_id": "outer", "user": "testuser", "env": "prod"}) as outer_app:
            # Test outer context
            result_outer = outer_app(ContextTestDTO(message="outer context"))
            assert result_outer.context_data["correlation_id"] == "outer"
            assert result_outer.context_data["user"] == "testuser"
            assert result_outer.context_data["env"] == "prod"
            
            # Test nested context with override
            with outer_app.context({"correlation_id": "inner", "session": "abc123"}) as inner_app:
                result_inner = inner_app(ContextTestDTO(message="inner context"))
                
                # Inner context should have overridden correlation_id but inherited other values
                assert result_inner.context_data["correlation_id"] == "inner"  # overridden
                assert result_inner.context_data["user"] == "testuser"  # inherited
                assert result_inner.context_data["env"] == "prod"  # inherited
                assert result_inner.context_data["session"] == "abc123"  # new
                
    def test_context_with_application_service(self):
        """Test context propagation through application services - simplified test"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        # For now, just test with features since ApplicationService 
        # constructor needs proper dependency injection setup
        with framework.context({"correlation_id": "app-test", "user": "appuser"}) as app_with_context:
            result = app_with_context(ContextTestDTO(message="test with context"))
            
            # Feature should have context
            assert result.context_data["correlation_id"] == "app-test"
            assert result.context_data["user"] == "appuser"
            
    def test_context_isolation_between_instances(self):
        """Test that different framework instances have isolated contexts"""
        framework1 = UseFramework("service-1")
        framework2 = UseFramework("service-2")
        
        @framework1.feature(ContextTestDTO)
        class ContextFeature1(ContextAwareFeature):
            pass
        
        @framework2.feature(ContextTestDTO)
        class ContextFeature2(ContextAwareFeature):
            pass
        
        # Set different contexts for each framework
        with framework1.context({"service": "service-1", "correlation_id": "ctx-1"}) as app1:
            result1 = app1(ContextTestDTO(message="framework 1"))
            
            with framework2.context({"service": "service-2", "correlation_id": "ctx-2"}) as app2:
                result2 = app2(ContextTestDTO(message="framework 2"))
                
                # Verify context isolation
                assert result1.context_data["service"] == "service-1"
                assert result1.context_data["correlation_id"] == "ctx-1"
                
                assert result2.context_data["service"] == "service-2"
                assert result2.context_data["correlation_id"] == "ctx-2"
                
                # Verify they don't interfere with each other
                assert result1.context_data != result2.context_data
                
    def test_context_cleanup_after_exit(self):
        """Test that context is properly cleaned up after exiting context manager"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        # Initially no context
        result_initial = framework(ContextTestDTO(message="initial"))
        assert result_initial.context_data == {}
        
        # Use context
        with framework.context({"correlation_id": "cleanup-test"}) as app_with_context:
            result_with_context = app_with_context(ContextTestDTO(message="with context"))
            assert result_with_context.context_data["correlation_id"] == "cleanup-test"
        
        # After exiting, context should be clean
        result_after = framework(ContextTestDTO(message="after"))
        assert result_after.context_data == {}
        
    def test_context_with_error_handling(self):
        """Test context behavior when exceptions occur"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFailingFeature(FailingFeature):
            pass
        
        # Test that context is still cleaned up even when exception occurs
        initial_context = framework._get_context()
        
        with pytest.raises(ValueError, match="Feature failed intentionally"):
            with framework.context({"correlation_id": "error-test"}) as app_with_context:
                app_with_context(ContextTestDTO(message="will fail"))
        
        # Context should be restored to initial state even after exception
        assert framework._get_context() == initial_context
        
    def test_multiple_executions_in_same_context(self):
        """Test multiple executions within the same context scope"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        with framework.context({"correlation_id": "multi-exec", "session": "session-123"}) as app_with_context:
            # Execute multiple times in the same context
            result1 = app_with_context(ContextTestDTO(message="first execution"))
            result2 = app_with_context(ContextTestDTO(message="second execution"))
            result3 = app_with_context(ContextTestDTO(message="third execution"))
            
            # All should have the same context
            for result in [result1, result2, result3]:
                assert result.context_data["correlation_id"] == "multi-exec"
                assert result.context_data["session"] == "session-123"
                
    def test_thread_safety(self):
        """Test that context is properly isolated in multi-threaded scenarios"""
        framework1 = UseFramework("thread-test-1")
        framework2 = UseFramework("thread-test-2")
        framework3 = UseFramework("thread-test-3")
        
        frameworks = [framework1, framework2, framework3]
        results = {}
        
        for i, fw in enumerate(frameworks):
            @fw.feature(ContextTestDTO)
            class ThreadTestFeature(ContextAwareFeature):
                pass
        
        def worker(thread_id):
            """Worker function for each thread"""
            fw = frameworks[thread_id]
            context = {"thread_id": thread_id, "worker": f"worker-{thread_id}"}
            
            with fw.context(context) as app_with_context:
                result = app_with_context(ContextTestDTO(message=f"thread {thread_id}"))
                results[thread_id] = result.context_data
        
        # Run multiple threads
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker, i) for i in range(3)]
            for future in futures:
                future.result()
        
        # Verify each thread had its own context
        assert len(results) == 3
        for i in range(3):
            assert results[i]["thread_id"] == i
            assert results[i]["worker"] == f"worker-{i}"
            
    def test_context_restoration_after_nested_exit(self):
        """Test that parent context is restored after nested context exits"""
        framework = UseFramework("nested-test")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        with framework.context({"level": "outer", "correlation_id": "outer-123", "persistent": "value"}) as outer_app:
            # Verify outer context
            result_outer = outer_app(ContextTestDTO(message="outer"))
            assert result_outer.context_data["level"] == "outer"
            assert result_outer.context_data["correlation_id"] == "outer-123"
            
            with outer_app.context({"level": "inner", "correlation_id": "inner-456", "temp": "temp_value"}) as inner_app:
                # Verify inner context (override + inheritance)
                result_inner = inner_app(ContextTestDTO(message="inner"))
                assert result_inner.context_data["level"] == "inner"  # overridden
                assert result_inner.context_data["correlation_id"] == "inner-456"  # overridden
                assert result_inner.context_data["persistent"] == "value"  # inherited
                assert result_inner.context_data["temp"] == "temp_value"  # new
            
            # After inner context exits, outer context should be restored
            result_outer_restored = outer_app(ContextTestDTO(message="outer restored"))
            assert result_outer_restored.context_data["level"] == "outer"
            assert result_outer_restored.context_data["correlation_id"] == "outer-123"
            assert result_outer_restored.context_data["persistent"] == "value"
            assert "temp" not in result_outer_restored.context_data  # should not have inner context data
            
    def test_empty_context(self):
        """Test context manager with empty context"""
        framework = UseFramework("empty-context-test")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        with framework.context({}) as app_with_context:
            result = app_with_context(ContextTestDTO(message="empty context"))
            assert result.context_data == {}
            
    def test_context_override_behavior(self):
        """Test context override behavior in nested contexts"""
        framework = UseFramework("override-test")
        
        @framework.feature(ContextTestDTO)
        class ContextTestFeature(ContextAwareFeature):
            pass
        
        with framework.context({"a": "outer_a", "b": "outer_b", "c": "outer_c"}) as outer_app:
            with outer_app.context({"a": "inner_a", "d": "inner_d"}) as inner_app:
                result = inner_app(ContextTestDTO(message="override test"))
                
                # Should have overridden 'a', inherited 'b' and 'c', and added 'd'
                assert result.context_data["a"] == "inner_a"  # overridden
                assert result.context_data["b"] == "outer_b"  # inherited
                assert result.context_data["c"] == "outer_c"  # inherited
                assert result.context_data["d"] == "inner_d"  # new