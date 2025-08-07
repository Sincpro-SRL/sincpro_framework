"""
Tests for simple typed context functionality in Sincpro Framework
"""

from typing_extensions import NotRequired, TypedDict

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)


# Context TypedDict definitions for testing
class ExampleContextKeys(TypedDict, total=False):
    """Example TypedDict for testing context typing"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    ENVIRONMENT: NotRequired[str]


class ExtendedContextKeys(TypedDict, total=False):
    """Extended context with more keys"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]
    REQUEST_ID: NotRequired[str]


# Test DTOs
class TypedContextTestDTO(DataTransferObject):
    """Test DTO for typed context features"""
    message: str


class TypedContextResponseDTO(DataTransferObject):
    """Response DTO for typed context tests"""
    result: str
    token: str = ""
    user_id: str = ""
    has_token: bool = False


class AppServiceTestDTO(DataTransferObject):
    """DTO for application service tests"""
    operation: str


class AppServiceResponseDTO(DataTransferObject):
    """Response DTO for application service tests"""
    result: str
    feature_result: str = ""
    token: str = ""


class TestSimpleTypedContext:
    """Test suite for simple typed context functionality"""

    def test_framework_with_typed_context(self):
        """Test creating framework instance with typed context"""
        # This should work - generic type parameter
        framework: UseFramework[ExampleContextKeys] = UseFramework("test-typed")
        
        # Framework should still work normally
        assert framework is not None
        assert hasattr(framework, 'context')

    def test_feature_with_typed_context_access(self):
        """Test that features can access typed context"""
        framework: UseFramework[ExampleContextKeys] = UseFramework("test-feature")
        
        @framework.feature(TypedContextTestDTO)
        class TypedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                # Access context - should be typed for type checkers
                token = self.context.get("TOKEN")  # Should be typed as str | None
                user_id = self.context.get("USER_ID")  # Should be typed as str | None
                
                return TypedContextResponseDTO(
                    result=f"Processed {dto.message}",
                    token=token or "",
                    user_id=user_id or "",
                    has_token=token is not None
                )
        
        # Test without context
        request = TypedContextTestDTO(message="test message")
        response = framework(request, TypedContextResponseDTO)
        
        assert response.result == "Processed test message"
        assert response.token == ""
        assert response.user_id == ""
        assert response.has_token is False

    def test_feature_with_context_data(self):
        """Test feature execution with actual context data"""
        framework: UseFramework[ExampleContextKeys] = UseFramework("test-with-data")
        
        @framework.feature(TypedContextTestDTO)
        class TypedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                token = self.context.get("TOKEN")
                user_id = self.context.get("USER_ID")
                
                return TypedContextResponseDTO(
                    result=f"Processed {dto.message}",
                    token=token or "",
                    user_id=user_id or "",
                    has_token=token is not None
                )
        
        # Test with context
        request = TypedContextTestDTO(message="with context")
        
        with framework.context({
            "TOKEN": "abc123",
            "USER_ID": "user456",
            "ENVIRONMENT": "test"
        }) as fw:
            response = fw(request, TypedContextResponseDTO)
            
            assert response.result == "Processed with context"
            assert response.token == "abc123"
            assert response.user_id == "user456"
            assert response.has_token is True

    def test_application_service_with_typed_context(self):
        """Test that application services also work with typed context"""
        framework: UseFramework[ExampleContextKeys] = UseFramework("test-app-service")
        
        @framework.feature(TypedContextTestDTO)
        class TypedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                token = self.context.get("TOKEN")
                return TypedContextResponseDTO(
                    result=f"Feature: {dto.message}",
                    token=token or "",
                    has_token=token is not None
                )
        
        @framework.app_service(AppServiceTestDTO)
        class TypedAppService(ApplicationService[AppServiceTestDTO, AppServiceResponseDTO]):
            def execute(self, dto: AppServiceTestDTO) -> AppServiceResponseDTO:
                # Access context in app service
                token = self.context.get("TOKEN")
                
                # Call feature
                feature_response = self.feature_bus.execute(
                    TypedContextTestDTO(message=dto.operation),
                    TypedContextResponseDTO
                )
                
                return AppServiceResponseDTO(
                    result=f"AppService: {dto.operation}",
                    feature_result=feature_response.result,
                    token=token or ""
                )
        
        # Test with context
        request = AppServiceTestDTO(operation="test operation")
        
        with framework.context({
            "TOKEN": "service-token",
            "USER_ID": "service-user"
        }) as fw:
            response = fw(request, AppServiceResponseDTO)
            
            assert response.result == "AppService: test operation"
            assert response.feature_result == "Feature: test operation"
            assert response.token == "service-token"

    def test_backward_compatibility(self):
        """Test that the framework still works without type parameters"""
        # Should work without generic type parameter
        framework = UseFramework("test-backward-compat")
        
        @framework.feature(TypedContextTestDTO)
        class RegularFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                # Should still work with regular dict access
                token = self.context.get("TOKEN")
                return TypedContextResponseDTO(
                    result=f"Backward compatible: {dto.message}",
                    token=token or "",
                    has_token=token is not None
                )
        
        request = TypedContextTestDTO(message="backward test")
        
        with framework.context({"TOKEN": "compat-token"}) as fw:
            response = fw(request, TypedContextResponseDTO)
            
            assert response.result == "Backward compatible: backward test"
            assert response.token == "compat-token"
            assert response.has_token is True

    def test_mixed_context_access(self):
        """Test accessing both typed and untyped context keys"""
        framework: UseFramework[ExampleContextKeys] = UseFramework("test-mixed")
        
        @framework.feature(TypedContextTestDTO)
        class MixedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                # Access typed keys
                token = self.context.get("TOKEN")
                user_id = self.context.get("USER_ID")
                
                # Access any key (for backward compatibility)
                custom_key = self.context.get("CUSTOM_KEY", "default")
                
                return TypedContextResponseDTO(
                    result=f"Mixed: {dto.message}, custom: {custom_key}",
                    token=token or "",
                    user_id=user_id or "",
                    has_token=token is not None
                )
        
        request = TypedContextTestDTO(message="mixed test")
        
        with framework.context({
            "TOKEN": "mixed-token",
            "USER_ID": "mixed-user",
            "CUSTOM_KEY": "custom-value"  # Not in TypedDict but should still work
        }) as fw:
            response = fw(request, TypedContextResponseDTO)
            
            assert response.result == "Mixed: mixed test, custom: custom-value"
            assert response.token == "mixed-token"
            assert response.user_id == "mixed-user"
            assert response.has_token is True