"""
Tests for the Context Manager functionality in Sincpro Framework
"""

import pytest
from unittest.mock import patch
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from sincpro_framework import (
    UseFramework, 
    ContextManager, 
    ContextConfig, 
    get_current_context,
    DataTransferObject,
    Feature,
    ApplicationService
)


class ContextTestDTO(DataTransferObject):
    """Test DTO for context manager tests"""
    message: str
    user_id: str = "test_user"


class ContextTestResponseDTO(DataTransferObject):
    """Test response DTO"""
    result: str
    context_data: dict = {}


class ContextAwareFeature(Feature):
    """Test feature that uses context"""
    
    def execute(self, dto: ContextTestDTO) -> ContextTestResponseDTO:
        # Access context through the injected framework_context function
        current_context = self.context
        correlation_id = self.get_context_value("correlation_id", "no-correlation")
        user_id = self.get_context_value("user.id", "unknown-user")
        
        return ContextTestResponseDTO(
            result=f"Processed: {dto.message}",
            context_data={
                "correlation_id": correlation_id,
                "user_id": user_id,
                "full_context": current_context
            }
        )


class TestAppServiceDTO(DataTransferObject):
    """Test DTO for application service tests"""
    message: str
    user_id: str = "app_test_user"


class TestAppServiceResponseDTO(DataTransferObject):
    """Test response DTO for application service"""
    result: str
    context_data: dict = {}


class ContextAwareApplicationService(ApplicationService):
    """Test application service that uses context"""
    
    def execute(self, dto: TestAppServiceDTO) -> TestAppServiceResponseDTO:
        # First execute a feature
        feature_dto = ContextTestDTO(message=dto.message, user_id=dto.user_id)
        feature_result = self.feature_bus.execute(feature_dto)
        
        # Access context in the application service
        correlation_id = self.get_context_value("correlation_id", "no-correlation")
        service_context = self.context
        
        return TestAppServiceResponseDTO(
            result=f"Service processed: {feature_result.result}",
            context_data={
                "service_correlation_id": correlation_id,
                "service_context": service_context,
                "feature_context": feature_result.context_data
            }
        )


class TestContextManager:
    """Test the basic ContextManager functionality"""
    
    def test_context_manager_basic_functionality(self):
        """Test basic context manager enter/exit"""
        config = ContextConfig(default_attrs={"service": "test"})
        manager = ContextManager(config)
        manager.context_attrs = {"correlation_id": "123", "user.id": "456"}
        
        # Before entering context
        assert get_current_context() == {}
        
        with manager as ctx:
            # Inside context
            current = get_current_context()
            assert current["correlation_id"] == "123"
            assert current["user.id"] == "456"
            assert current["service"] == "test"
            
            # Test set/get/clear operations
            ctx.set("temp_key", "temp_value")
            assert ctx.get("temp_key") == "temp_value"
            assert ctx.clear("temp_key") is True
            assert ctx.get("temp_key") is None
        
        # After exiting context
        assert get_current_context() == {}

    def test_nested_context_managers(self):
        """Test nested context managers with override support"""
        config = ContextConfig()
        
        outer_manager = ContextManager(config)
        outer_manager.context_attrs = {"level": "outer", "shared": "outer_value"}
        
        inner_manager = ContextManager(config)
        inner_manager.context_attrs = {"level": "inner", "inner_only": "inner_value"}
        
        with outer_manager:
            outer_context = get_current_context()
            assert outer_context["level"] == "outer"
            assert outer_context["shared"] == "outer_value"
            
            with inner_manager:
                inner_context = get_current_context()
                assert inner_context["level"] == "inner"  # Override
                assert inner_context["shared"] == "outer_value"  # Inherited
                assert inner_context["inner_only"] == "inner_value"  # New
            
            # Back to outer context
            restored_context = get_current_context()
            assert restored_context["level"] == "outer"
            assert restored_context["shared"] == "outer_value"
            assert "inner_only" not in restored_context

    def test_context_validation(self):
        """Test context validation with allowed keys and type checking"""
        config = ContextConfig(
            allowed_keys={"correlation_id", "user.id"},
            validate_types=True,
            max_key_length=20,
            max_value_length=50
        )
        
        manager = ContextManager(config)
        
        # Test allowed keys
        manager.context_attrs = {"correlation_id": "123"}
        with manager:
            pass  # Should work
        
        # Test disallowed key
        manager.context_attrs = {"invalid_key": "value"}
        with pytest.raises(ValueError, match="not in allowed keys"):
            with manager:
                pass
        
        # Test key length validation
        manager.context_attrs = {"a" * 25: "value"}
        with pytest.raises(ValueError, match="exceeds maximum length"):
            with manager:
                pass
        
        # Test value length validation
        manager.context_attrs = {"correlation_id": "a" * 100}
        with pytest.raises(ValueError, match="exceeds maximum length"):
            with manager:
                pass

    def test_context_exception_handling(self):
        """Test that exceptions are properly handled and enriched with context"""
        config = ContextConfig()
        manager = ContextManager(config)
        manager.context_attrs = {"correlation_id": "error-test", "user.id": "test-user"}
        
        with pytest.raises(ValueError) as exc_info:
            with manager:
                current_context = get_current_context()
                assert current_context["correlation_id"] == "error-test"
                raise ValueError("Test exception")
        
        # Check that exception was enriched with context info
        exception = exc_info.value
        assert hasattr(exception, 'context_info')
        assert exception.context_info["context_data"]["correlation_id"] == "error-test"
        assert exception.context_info["exception_type"] == "ValueError"

    def test_thread_safety(self):
        """Test that context is properly isolated between threads"""
        config = ContextConfig()
        results = {}
        
        def worker(thread_id):
            manager = ContextManager(config)
            manager.context_attrs = {"thread_id": thread_id, "worker": f"worker-{thread_id}"}
            
            with manager:
                time.sleep(0.1)  # Simulate some work
                context = get_current_context()
                results[thread_id] = context
        
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


class TestFrameworkIntegration:
    """Test context manager integration with UseFramework"""
    
    def test_framework_context_configuration(self):
        """Test framework context configuration"""
        framework = UseFramework("test-service")
        
        # Configure context manager
        framework.configure_context_manager(
            default_attrs={"service.name": "test-service"},
            allowed_keys={"correlation_id", "user.id", "feature_flag"},
            validate_types=True
        )
        
        # Test context creation
        context_manager = framework.context({"correlation_id": "123", "user.id": "test-user"})
        assert isinstance(context_manager, ContextManager)

    def test_framework_execution_with_context(self):
        """Test framework execution within context manager"""
        framework = UseFramework("test-service")
        framework.configure_context_manager(
            default_attrs={"service.name": "test-service"}
        )
        
        # Register a test feature
        @framework.feature(ContextTestDTO)
        class TestFeature(ContextAwareFeature):
            pass
        
        # Execute without context
        result_no_context = framework(ContextTestDTO(message="test without context"))
        assert result_no_context.context_data["correlation_id"] == "no-correlation"
        
        # Execute with context
        with framework.context({"correlation_id": "test-123", "user.id": "user-456"}):
            result_with_context = framework(ContextTestDTO(message="test with context"))
            
            assert result_with_context.context_data["correlation_id"] == "test-123"
            assert result_with_context.context_data["user_id"] == "user-456"
            assert result_with_context.context_data["full_context"]["service.name"] == "test-service"

    def test_application_service_context_inheritance(self):
        """Test that ApplicationServices inherit context properly"""
        framework = UseFramework("test-service")
        framework.configure_context_manager(
            default_attrs={"service.name": "test-service"}
        )
        
        # Register feature and application service with different DTOs
        @framework.feature(ContextTestDTO)
        class TestFeature(ContextAwareFeature):
            pass
        
        @framework.app_service(TestAppServiceDTO)
        class TestAppService(ContextAwareApplicationService):
            pass
        
        # Execute application service with context
        with framework.context({"correlation_id": "app-service-test", "user.id": "app-user"}):
            result = framework(TestAppServiceDTO(message="app service test"))
            
            # Check that both service and feature received the context
            assert result.context_data["service_correlation_id"] == "app-service-test"
            assert result.context_data["feature_context"]["correlation_id"] == "app-service-test"
            assert result.context_data["service_context"]["service.name"] == "test-service"

    def test_context_with_multiple_executions(self):
        """Test that context persists across multiple executions in the same scope"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class TestFeature(ContextAwareFeature):
            pass
        
        with framework.context({"correlation_id": "multi-exec", "session": "session-123"}):
            # Execute multiple times in the same context
            result1 = framework(ContextTestDTO(message="first execution"))
            result2 = framework(ContextTestDTO(message="second execution"))
            
            # Both should have the same context
            assert result1.context_data["correlation_id"] == "multi-exec"
            assert result2.context_data["correlation_id"] == "multi-exec"
            assert result1.context_data["full_context"]["session"] == "session-123"
            assert result2.context_data["full_context"]["session"] == "session-123"

    def test_error_handling_with_context(self):
        """Test error handling when context is active"""
        framework = UseFramework("test-service")
        
        @framework.feature(ContextTestDTO)
        class FailingFeature(Feature):
            def execute(self, dto: ContextTestDTO) -> ContextTestResponseDTO:
                raise ValueError("Feature failed")
        
        with pytest.raises(ValueError) as exc_info:
            with framework.context({"correlation_id": "error-context", "user.id": "error-user"}):
                framework(ContextTestDTO(message="this will fail"))
        
        # Exception should be enriched with context
        exception = exc_info.value
        assert hasattr(exception, 'context_info')
        assert exception.context_info["context_data"]["correlation_id"] == "error-context"

    def test_context_isolation_between_framework_instances(self):
        """Test that different framework instances have isolated contexts"""
        framework1 = UseFramework("service-1")
        framework2 = UseFramework("service-2")
        
        @framework1.feature(ContextTestDTO)
        class Feature1(ContextAwareFeature):
            pass
        
        @framework2.feature(ContextTestDTO)
        class Feature2(ContextAwareFeature):
            pass
        
        # Use different contexts for each framework
        with framework1.context({"service": "service-1", "correlation_id": "ctx-1"}):
            result1 = framework1(ContextTestDTO(message="framework 1"))
            
            with framework2.context({"service": "service-2", "correlation_id": "ctx-2"}):
                result2 = framework2(ContextTestDTO(message="framework 2"))
                
                # Verify isolation
                assert result1.context_data["correlation_id"] == "ctx-1"
                assert result2.context_data["correlation_id"] == "ctx-2"