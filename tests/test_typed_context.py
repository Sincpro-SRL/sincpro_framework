"""
Tests for typed context functionality in Sincpro Framework
"""

from typing import Any, Dict
from typing_extensions import NotRequired, TypedDict

import pytest

from sincpro_framework import (
    ApplicationService,
    ContextTypeMixin,
    DataTransferObject,
    Feature,
    TypedContext,
    UseFramework,
    create_typed_context,
)


# Context TypedDict definitions for testing
class ExampleContextKeys(TypedDict, total=False):
    """Example TypedDict for testing context typing"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    ENVIRONMENT: NotRequired[str]
    SESSION_ID: NotRequired[str]


class ExtendedContextKeys(TypedDict, total=False):
    """Extended context with more keys"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]
    REQUEST_ID: NotRequired[str]
    TENANT_ID: NotRequired[str]


# Test DTOs
class TypedContextTestDTO(DataTransferObject):
    """Test DTO for typed context features"""
    message: str


class TypedContextResponseDTO(DataTransferObject):
    """Response DTO for typed context tests"""
    result: str
    context_data: Dict[str, Any] = {}
    token: str = ""
    user_id: str = ""


# Context type mixins for testing
class ExampleContextType(ContextTypeMixin):
    """Test context mixin with ExampleContextKeys"""
    context: ExampleContextKeys


class ExtendedContextType(ContextTypeMixin):
    """Extended context mixin with more keys"""
    context: ExtendedContextKeys


class TestTypedContext:
    """Test the TypedContext wrapper class"""

    def test_typed_context_creation(self):
        """Test creating a TypedContext instance"""
        context_dict = {"key1": "value1", "key2": "value2"}
        typed_context = TypedContext(context_dict)
        
        assert typed_context._context == context_dict
        assert typed_context.raw_dict == context_dict

    def test_typed_context_get_method(self):
        """Test the get method of TypedContext"""
        context_dict = {"TOKEN": "abc123", "USER_ID": "user456"}
        typed_context = TypedContext(context_dict)
        
        assert typed_context.get("TOKEN") == "abc123"
        assert typed_context.get("USER_ID") == "user456"
        assert typed_context.get("NONEXISTENT", "default") == "default"
        assert typed_context.get("NONEXISTENT") is None

    def test_typed_context_dict_interface(self):
        """Test that TypedContext implements dict-like interface"""
        context_dict = {"key1": "value1", "key2": "value2"}
        typed_context = TypedContext(context_dict)
        
        # Test bracket access
        assert typed_context["key1"] == "value1"
        
        # Test assignment
        typed_context["key3"] = "value3"
        assert typed_context["key3"] == "value3"
        assert "key3" in context_dict  # Should update underlying dict
        
        # Test containment
        assert "key1" in typed_context
        assert "nonexistent" not in typed_context
        
        # Test length
        assert len(typed_context) == 3
        
        # Test keys, values, items
        assert list(typed_context.keys()) == list(context_dict.keys())
        assert list(typed_context.values()) == list(context_dict.values())
        assert list(typed_context.items()) == list(context_dict.items())

    def test_typed_context_copy_and_update(self):
        """Test copy and update methods"""
        context_dict = {"key1": "value1"}
        typed_context = TypedContext(context_dict)
        
        # Test copy
        copied = typed_context.copy()
        assert copied == context_dict
        assert copied is not context_dict  # Should be a new dict
        
        # Test update
        typed_context.update({"key2": "value2", "key3": "value3"})
        assert typed_context["key2"] == "value2"
        assert typed_context["key3"] == "value3"

    def test_create_typed_context_function(self):
        """Test the utility function for creating typed contexts"""
        context_dict = {"TOKEN": "xyz789", "USER_ID": "admin"}
        typed_context = create_typed_context(context_dict)
        
        assert isinstance(typed_context, TypedContext)
        assert typed_context.get("TOKEN") == "xyz789"
        assert typed_context.get("USER_ID") == "admin"


class TestContextTypeMixin:
    """Test the ContextTypeMixin functionality"""

    def test_context_type_mixin_methods(self):
        """Test ContextTypeMixin helper methods"""
        # Create a test class that uses the mixin
        class TestClass(ExampleContextType):
            def __init__(self):
                self.context = {"TOKEN": "test123", "USER_ID": "testuser"}
        
        instance = TestClass()
        
        # Test get_context_value
        assert instance.get_context_value("TOKEN") == "test123"
        assert instance.get_context_value("USER_ID") == "testuser"
        assert instance.get_context_value("NONEXISTENT", "default") == "default"
        
        # Test has_context_key
        assert instance.has_context_key("TOKEN") is True
        assert instance.has_context_key("USER_ID") is True
        assert instance.has_context_key("NONEXISTENT") is False

    def test_context_type_mixin_with_empty_context(self):
        """Test ContextTypeMixin with empty or missing context"""
        class TestClass(ExampleContextType):
            def __init__(self):
                self.context = {}
        
        instance = TestClass()
        
        # Should handle empty context gracefully
        assert instance.get_context_value("TOKEN", "default") == "default"
        assert instance.has_context_key("TOKEN") is False
        
        # Test with no context attribute
        class NoContextClass(ExampleContextType):
            pass
        
        no_context_instance = NoContextClass()
        assert no_context_instance.get_context_value("TOKEN", "default") == "default"
        assert no_context_instance.has_context_key("TOKEN") is False


class TestTypedContextFeatures:
    """Test typed context functionality with Features"""

    def test_feature_with_typed_context_mixin(self):
        """Test Feature using typed context mixin"""
        framework = UseFramework("typed-context-test")
        
        @framework.feature(TypedContextTestDTO)
        class TypedContextFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO], ExampleContextType):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                # Test type-safe context access using helper methods
                token = self.get_context_value("TOKEN", "default_token")
                user_id = self.get_context_value("USER_ID", "default_user")
                
                # Test direct context access
                environment = self.context.get("ENVIRONMENT", "dev")
                
                return TypedContextResponseDTO(
                    result=f"processed: {dto.message}",
                    context_data=self.context.copy() if self.context else {},
                    token=token,
                    user_id=user_id
                )
        
        # Test without context
        result_no_context = framework(TypedContextTestDTO(message="test"))
        assert result_no_context.token == "default_token"
        assert result_no_context.user_id == "default_user"
        
        # Test with context
        test_context = {
            "TOKEN": "abc123",
            "USER_ID": "testuser",
            "ENVIRONMENT": "production"
        }
        
        with framework.context(test_context) as app_with_context:
            result_with_context = app_with_context(TypedContextTestDTO(message="test"))
            assert result_with_context.token == "abc123"
            assert result_with_context.user_id == "testuser"
            assert result_with_context.context_data["ENVIRONMENT"] == "production"

    def test_feature_with_enhanced_context_methods(self):
        """Test Feature using enhanced context methods"""
        framework = UseFramework("enhanced-context-test")
        
        @framework.feature(TypedContextTestDTO)
        class EnhancedContextFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                # Test enhanced context methods from base Feature class
                token = self.get_context_value("TOKEN", "no_token")
                user_id = self.get_context_value("USER_ID", "no_user")
                
                # Test has_context_key method
                has_token = self.has_context_key("TOKEN")
                has_session = self.has_context_key("SESSION_ID")
                
                return TypedContextResponseDTO(
                    result=f"processed: {dto.message}",
                    context_data={
                        "has_token": has_token,
                        "has_session": has_session,
                        "full_context": self.context.copy() if self.context else {}
                    },
                    token=token,
                    user_id=user_id
                )
        
        # Test with context
        test_context = {"TOKEN": "xyz789", "USER_ID": "admin"}
        
        with framework.context(test_context) as app_with_context:
            result = app_with_context(TypedContextTestDTO(message="test"))
            assert result.token == "xyz789"
            assert result.user_id == "admin"
            assert result.context_data["has_token"] is True
            assert result.context_data["has_session"] is False


class TestTypedContextApplicationServices:
    """Test typed context functionality with ApplicationServices"""

    def test_application_service_with_typed_context_mixin(self):
        """Test ApplicationService using typed context mixin"""
        framework = UseFramework("app-service-typed-context-test")
        
        # Use unique DTOs to avoid conflicts
        class AppServiceTestDTO(DataTransferObject):
            message: str
        
        class FeatureTestDTO(DataTransferObject):
            message: str
        
        # First register a feature
        @framework.feature(FeatureTestDTO)
        class SimpleFeature(Feature[FeatureTestDTO, TypedContextResponseDTO]):
            def execute(self, dto: FeatureTestDTO) -> TypedContextResponseDTO:
                token = self.get_context_value("TOKEN", "feature_default")
                return TypedContextResponseDTO(
                    result=f"feature: {dto.message}",
                    token=token
                )
        
        # Now register an application service with typed context
        @framework.app_service(AppServiceTestDTO)
        class TypedContextAppService(ApplicationService[AppServiceTestDTO, TypedContextResponseDTO], ExtendedContextType):
            def execute(self, dto: AppServiceTestDTO) -> TypedContextResponseDTO:
                # Test type-safe context access
                correlation_id = self.get_context_value("CORRELATION_ID", "default_corr")
                user_id = self.get_context_value("USER_ID", "default_user")
                tenant_id = self.get_context_value("TENANT_ID", "default_tenant")
                
                # Execute a feature through feature_bus
                feature_result = self.feature_bus.execute(FeatureTestDTO(message="from_service"))
                
                return TypedContextResponseDTO(
                    result=f"service: {dto.message}",
                    context_data={
                        "correlation_id": correlation_id,
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "feature_result": feature_result.result if feature_result else "no_feature_result"
                    }
                )
        
        # Test with extended context
        test_context = {
            "TOKEN": "service_token",
            "USER_ID": "service_user",
            "CORRELATION_ID": "corr_123",
            "TENANT_ID": "tenant_456"
        }
        
        with framework.context(test_context) as app_with_context:
            result = app_with_context(AppServiceTestDTO(message="service_test"))
            assert result.context_data["correlation_id"] == "corr_123"
            assert result.context_data["user_id"] == "service_user"
            assert result.context_data["tenant_id"] == "tenant_456"
            assert "feature: from_service" in result.context_data["feature_result"]


class TestBackwardCompatibility:
    """Test that typed context doesn't break existing functionality"""

    def test_untyped_features_still_work(self):
        """Test that existing Features without typing still work"""
        framework = UseFramework("backward-compat-test")
        
        @framework.feature(TypedContextTestDTO)
        class LegacyFeature(Feature):
            def execute(self, dto):
                # Old-style context access should still work
                context_data = self.context.copy() if hasattr(self, 'context') and self.context else {}
                token = self.context.get("TOKEN", "legacy_default") if self.context else "legacy_default"
                
                return TypedContextResponseDTO(
                    result=f"legacy: {dto.message}",
                    context_data=context_data,
                    token=token
                )
        
        # Test without context
        result_no_context = framework(TypedContextTestDTO(message="legacy_test"))
        assert result_no_context.token == "legacy_default"
        
        # Test with context
        with framework.context({"TOKEN": "legacy_token"}) as app_with_context:
            result_with_context = app_with_context(TypedContextTestDTO(message="legacy_test"))
            assert result_with_context.token == "legacy_token"

    def test_mixed_typed_and_untyped(self):
        """Test mixing typed and untyped features in the same framework"""
        framework = UseFramework("mixed-test")
        
        # Typed feature
        @framework.feature(TypedContextTestDTO)
        class TypedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO], ExampleContextType):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                token = self.get_context_value("TOKEN", "typed_default")
                return TypedContextResponseDTO(
                    result=f"typed: {dto.message}",
                    token=token
                )
        
        # Test that both approaches can coexist
        test_context = {"TOKEN": "mixed_token", "USER_ID": "mixed_user"}
        
        with framework.context(test_context) as app_with_context:
            typed_result = app_with_context(TypedContextTestDTO(message="mixed_test"))
            assert typed_result.token == "mixed_token"


class TestRealWorldScenarios:
    """Test realistic usage scenarios"""

    def test_siat_soap_sdk_example(self):
        """Test the example from the issue description"""
        framework = UseFramework("siat-soap-sdk", log_features=False)
        
        # Mock dependencies
        class MockProxySiatServices:
            def get_client_for_service(self, wsdl, token=None, env=None):
                return f"Client({wsdl}, {token}, {env})"
        
        proxy_siat = MockProxySiatServices()
        framework.add_dependency("proxy_siat", proxy_siat)
        
        # Define context types as in the issue example
        class SIATEnvironment:
            PROD = "production"
            TEST = "testing"
        
        class KnownContextKeys(TypedDict, total=False):
            """Known context keys with their types"""
            TOKEN: NotRequired[str]
            SIAT_ENV: NotRequired[SIATEnvironment]
        
        class DependencyContextType(ContextTypeMixin):
            proxy_siat: MockProxySiatServices
            context: KnownContextKeys
            
            def soap_client(self, wsdl: str):
                """Helper function"""
                if self.context.get("TOKEN") and self.context.get("SIAT_ENV"):
                    return self.proxy_siat.get_client_for_service(
                        wsdl, self.context.get("TOKEN"), self.context.get("SIAT_ENV")
                    )
                return self.proxy_siat.get_client_for_service(wsdl)
        
        # Create Feature with the pattern from the issue (using unique DTO)
        class SIATTestDTO(DataTransferObject):
            message: str
        
        @framework.feature(SIATTestDTO)
        class SIATFeature(Feature, DependencyContextType):
            def execute(self, dto: SIATTestDTO) -> TypedContextResponseDTO:
                # Use the soap_client helper with typed context
                client = self.soap_client("test.wsdl")
                token = self.get_context_value("TOKEN", "no_token")
                
                return TypedContextResponseDTO(
                    result=f"SIAT: {dto.message}",
                    context_data={"client": str(client)},
                    token=token
                )
        
        # Test with context
        siat_context = {
            "TOKEN": "siat_token_123",
            "SIAT_ENV": SIATEnvironment.PROD
        }
        
        with framework.context(siat_context) as app_with_context:
            result = app_with_context(SIATTestDTO(message="siat_test"))
            assert result.token == "siat_token_123"
            assert "Client(test.wsdl, siat_token_123, production)" in result.context_data["client"]

    def test_nested_context_with_typed_access(self):
        """Test nested contexts with typed access"""
        framework = UseFramework("nested-typed-context-test")
        
        @framework.feature(TypedContextTestDTO)
        class NestedTypedFeature(Feature[TypedContextTestDTO, TypedContextResponseDTO], ExtendedContextType):
            def execute(self, dto: TypedContextTestDTO) -> TypedContextResponseDTO:
                correlation_id = self.get_context_value("CORRELATION_ID", "no_corr")
                user_id = self.get_context_value("USER_ID", "no_user")
                request_id = self.get_context_value("REQUEST_ID", "no_request")
                
                return TypedContextResponseDTO(
                    result=f"nested: {dto.message}",
                    context_data={
                        "correlation_id": correlation_id,
                        "user_id": user_id,
                        "request_id": request_id
                    }
                )
        
        # Test nested contexts
        with framework.context({
            "CORRELATION_ID": "outer_corr",
            "USER_ID": "outer_user",
            "TENANT_ID": "outer_tenant"
        }) as outer_app:
            with outer_app.context({
                "CORRELATION_ID": "inner_corr",  # Override
                "REQUEST_ID": "inner_request"    # New
            }) as inner_app:
                result = inner_app(TypedContextTestDTO(message="nested_test"))
                assert result.context_data["correlation_id"] == "inner_corr"  # Overridden
                assert result.context_data["user_id"] == "outer_user"         # Inherited
                assert result.context_data["request_id"] == "inner_request"   # New