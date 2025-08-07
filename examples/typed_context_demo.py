"""
Example demonstrating the new typed context functionality in Sincpro Framework

This example shows the solution to the issue raised about improving context typing.
It demonstrates how developers can now use TypedDict to define known context keys
and get type safety when accessing context values.
"""

from typing_extensions import NotRequired, TypedDict

from sincpro_framework import (
    ApplicationService,
    ContextTypeMixin,
    DataTransferObject,
    Feature,
    UseFramework,
)


# Example 1: Define your context structure using TypedDict
class KnownContextKeys(TypedDict, total=False):
    """Known context keys with their types - from the original issue"""
    TOKEN: NotRequired[str]
    SIAT_ENV: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]


# Example 2: Create a context type mixin as suggested in the issue
class DependencyContextType(ContextTypeMixin):
    """Context type mixin that provides typed context access"""
    context: KnownContextKeys  # This gives IDE support for context keys

    def get_token(self) -> str | None:
        """Helper method to get token with proper typing"""
        return self.context.get("TOKEN")

    def get_user_id(self) -> str | None:
        """Helper method to get user ID with proper typing"""
        return self.context.get("USER_ID")


# Example 3: Define DTOs
class ProcessRequestDTO(DataTransferObject):
    """Request DTO for features"""
    operation: str
    data: str


class ServiceRequestDTO(DataTransferObject):
    """Request DTO for application services"""
    operation: str
    data: str


class ProcessResponseDTO(DataTransferObject):
    """Response DTO for processing"""
    result: str
    token_used: str = ""
    user_id: str = ""
    correlation_id: str = ""


# Example 4: Create Features with typed context (as in the original issue)
class Feature(Feature, DependencyContextType):
    """Feature with typed context access"""
    pass


class ApplicationService(ApplicationService, DependencyContextType):
    """ApplicationService with typed context access"""
    pass


def main():
    """Demonstrate the typed context functionality"""
    
    # Initialize framework
    framework = UseFramework("siat-soap-sdk", log_features=False)
    
    # Mock dependencies as in the original issue
    class ProxySiatServices:
        def get_client_for_service(self, wsdl: str, token: str = None, env: str = None):
            return f"SoapClient(wsdl={wsdl}, token={token}, env={env})"
    
    proxy_siat = ProxySiatServices()
    framework.add_dependency("proxy_siat", proxy_siat)
    
    # Example 5: Register Features using the new pattern
    @framework.feature(ProcessRequestDTO)
    class TokenValidationFeature(Feature):
        """Feature that validates tokens using typed context"""
        
        def execute(self, dto: ProcessRequestDTO) -> ProcessResponseDTO:
            # Type-safe context access using helper methods
            token = self.get_token()  # Returns str | None with proper typing
            user_id = self.get_user_id()  # Returns str | None with proper typing
            
            # Direct context access also works with typing
            correlation_id = self.context.get("CORRELATION_ID", "unknown")
            
            # Business logic with context
            if token:
                result = f"Validated {dto.operation} for user {user_id or 'anonymous'}"
            else:
                result = f"No token provided for {dto.operation}"
            
            return ProcessResponseDTO(
                result=result,
                token_used=token or "none",
                user_id=user_id or "anonymous",
                correlation_id=correlation_id
            )
    
    # Example 6: Register ApplicationServices using the new pattern
    @framework.app_service(ServiceRequestDTO)
    class ProcessOrchestrationService(ApplicationService):
        """Application service that orchestrates processing with typed context"""
        
        def execute(self, dto: ServiceRequestDTO) -> ProcessResponseDTO:
            # Type-safe context access
            token = self.get_token()
            correlation_id = self.get_context_value("CORRELATION_ID", "service-generated")
            
            # Execute feature through feature_bus
            feature_dto = ProcessRequestDTO(operation=dto.operation, data=dto.data)
            feature_result = self.feature_bus.execute(feature_dto)
            
            # Enhance result with service-level processing
            enhanced_result = f"Service: {feature_result.result} (correlation: {correlation_id})"
            
            return ProcessResponseDTO(
                result=enhanced_result,
                token_used=feature_result.token_used,
                user_id=feature_result.user_id,
                correlation_id=correlation_id
            )
    
    print("=== Typed Context Demo ===\n")
    
    # Example 7: Execute without context
    print("1. Execute without context:")
    request = ProcessRequestDTO(operation="validate", data="test_data")
    result_no_context = framework(request)
    print(f"   Result: {result_no_context.result}")
    print(f"   Token: {result_no_context.token_used}")
    print(f"   User: {result_no_context.user_id}")
    print()
    
    # Example 8: Execute with typed context (using ApplicationService)
    print("2. Execute with typed context (ApplicationService):")
    context_with_types = {
        "TOKEN": "abc123token",
        "SIAT_ENV": "production",
        "USER_ID": "admin@company.com",
        "CORRELATION_ID": "req-456789"
    }
    
    with framework.context(context_with_types) as app_with_context:
        service_request = ServiceRequestDTO(operation="validate", data="test_data")
        result_with_context = app_with_context(service_request)
        print(f"   Result: {result_with_context.result}")
        print(f"   Token: {result_with_context.token_used}")
        print(f"   User: {result_with_context.user_id}")
        print(f"   Correlation: {result_with_context.correlation_id}")
    print()
    
    # Example 9: Nested contexts with typed access
    print("3. Nested contexts with type safety:")
    with framework.context({
        "TOKEN": "outer-token",
        "USER_ID": "outer-user",
        "SIAT_ENV": "staging"
    }) as outer_app:
        print("   Outer context established...")
        
        with outer_app.context({
            "TOKEN": "inner-token",  # Override
            "CORRELATION_ID": "inner-correlation"  # New
        }) as inner_app:
            service_request = ServiceRequestDTO(operation="validate", data="nested_data")
            result_nested = inner_app(service_request)
            print(f"   Nested result: {result_nested.result}")
            print(f"   Token (overridden): {result_nested.token_used}")
            print(f"   User (inherited): {result_nested.user_id}")
            print(f"   Correlation (new): {result_nested.correlation_id}")
    print()
    
    # Example 10: Show backward compatibility
    print("4. Backward compatibility - old dict access still works:")
    
    # Use a different framework instance to avoid DTO conflicts
    legacy_framework = UseFramework("legacy-test")
    
    @legacy_framework.feature(ProcessRequestDTO) 
    class LegacyTestFeature(Feature):
        """Legacy feature using old dict-style context access"""
        
        def execute(self, dto: ProcessRequestDTO) -> ProcessResponseDTO:
            # Old style still works
            token = self.context.get("TOKEN", "legacy_default") if self.context else "no_context"
            user_id = self.context.get("USER_ID", "legacy_user") if self.context else "no_context"
            
            return ProcessResponseDTO(
                result=f"Legacy: {dto.operation}",
                token_used=token,
                user_id=user_id
            )
    
    with legacy_framework.context({"TOKEN": "legacy_token", "USER_ID": "legacy_user"}) as legacy_app:
        legacy_result = legacy_app(request)
        print(f"   Legacy result: {legacy_result.result}")
        print(f"   Legacy token: {legacy_result.token_used}")
        print(f"   Legacy user: {legacy_result.user_id}")
    print()
    
    print("=== Demo completed successfully! ===")
    print("The new typed context system provides:")
    print("✅ Type safety for known context keys")
    print("✅ IDE autocompletion and hints")
    print("✅ Helper methods for common access patterns")
    print("✅ Full backward compatibility")
    print("✅ Support for both Feature and ApplicationService patterns")


if __name__ == "__main__":
    main()