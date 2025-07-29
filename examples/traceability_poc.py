"""
POC (Proof of Concept) for Sincpro Framework Traceability Feature

This example demonstrates the new traceability capabilities including:
- Basic traceability with traceability=True
- Span creation with span=True
- Correlation ID propagation
- OpenTelemetry integration
"""

from sincpro_framework import UseFramework, Feature, ApplicationService, DataTransferObject


# DTO Definitions
class CreateUserCommand(DataTransferObject):
    name: str
    email: str


class UserCreatedResponse(DataTransferObject):
    user_id: str
    message: str


class ValidateUserCommand(DataTransferObject):
    email: str


class ValidationResult(DataTransferObject):
    is_valid: bool
    message: str


class SendWelcomeEmailCommand(DataTransferObject):
    user_id: str
    email: str


class EmailSentResponse(DataTransferObject):
    success: bool
    message: str


class ProcessUserRegistrationCommand(DataTransferObject):
    name: str
    email: str


class RegistrationResult(DataTransferObject):
    success: bool
    user_id: str
    message: str


# Initialize Framework with observability
app = UseFramework("user-registration-service")

# Enable observability (this enables OpenTelemetry tracing)
app.enable_observability(
    service_name="user-registration-service",
    jaeger_endpoint=None  # Uses console exporter for demo
)


# Features with different traceability configurations

@app.feature(ValidateUserCommand, traceability=True)
class ValidateUserFeature(Feature):
    """Validates user email with basic traceability."""
    
    def execute(self, dto: ValidateUserCommand) -> ValidationResult:
        # Simple validation logic
        is_valid = "@" in dto.email and len(dto.email) > 5
        
        return ValidationResult(
            is_valid=is_valid,
            message="Email is valid" if is_valid else "Invalid email format"
        )


@app.feature(CreateUserCommand, traceability=True, span=True)
class CreateUserFeature(Feature):
    """Creates user with full tracing and span creation."""
    
    def execute(self, dto: CreateUserCommand) -> UserCreatedResponse:
        # Simulate user creation
        user_id = f"user_{hash(dto.email) % 10000}"
        
        return UserCreatedResponse(
            user_id=user_id,
            message=f"User {dto.name} created successfully"
        )


@app.feature(SendWelcomeEmailCommand, span=True)
class SendWelcomeEmailFeature(Feature):
    """Sends welcome email with span tracking."""
    
    def execute(self, dto: SendWelcomeEmailCommand) -> EmailSentResponse:
        # Simulate email sending
        success = True  # In real scenario, this would call email service
        
        return EmailSentResponse(
            success=success,
            message=f"Welcome email sent to user {dto.user_id}"
        )


# Application Service with orchestration and traceability
@app.app_service(ProcessUserRegistrationCommand, traceability=True, span=True)
class UserRegistrationService(ApplicationService):
    """Orchestrates the complete user registration process with full observability."""
    
    def execute(self, dto: ProcessUserRegistrationCommand) -> RegistrationResult:
        try:
            # Step 1: Validate email
            validation_result = self.feature_bus.execute(
                ValidateUserCommand(email=dto.email)
            )
            
            if not validation_result.is_valid:
                return RegistrationResult(
                    success=False,
                    user_id="",
                    message=f"Registration failed: {validation_result.message}"
                )
            
            # Step 2: Create user
            user_created = self.feature_bus.execute(
                CreateUserCommand(name=dto.name, email=dto.email)
            )
            
            # Step 3: Send welcome email
            email_result = self.feature_bus.execute(
                SendWelcomeEmailCommand(
                    user_id=user_created.user_id,
                    email=dto.email
                )
            )
            
            return RegistrationResult(
                success=True,
                user_id=user_created.user_id,
                message=f"Registration completed. {email_result.message}"
            )
            
        except Exception as e:
            return RegistrationResult(
                success=False,
                user_id="",
                message=f"Registration failed: {str(e)}"
            )


def main():
    """Demonstrate the traceability features."""
    print("🚀 Sincpro Framework Traceability POC")
    print("=" * 50)
    
    # Example 1: Basic feature execution with traceability
    print("\n📧 1. Testing email validation (traceability=True)")
    validation_cmd = ValidateUserCommand(email="user@example.com")
    validation_result = app(validation_cmd, ValidationResult)
    print(f"   Result: {validation_result.message}")
    
    # Example 2: Feature with span creation
    print("\n👤 2. Testing user creation (traceability=True, span=True)")
    create_cmd = CreateUserCommand(name="John Doe", email="john@example.com")
    create_result = app(create_cmd, UserCreatedResponse)
    print(f"   Result: {create_result.message}")
    
    # Example 3: Complete flow with correlation ID
    print("\n🔄 3. Testing complete registration flow with correlation ID")
    registration_cmd = ProcessUserRegistrationCommand(
        name="Jane Smith",
        email="jane@example.com"
    )
    
    # Execute with correlation ID for request tracking
    registration_result = app(
        registration_cmd, 
        RegistrationResult,
        correlation_id="reg-req-12345"
    )
    
    print(f"   Result: {registration_result.message}")
    print(f"   User ID: {registration_result.user_id}")
    print(f"   Success: {registration_result.success}")
    
    # Example 4: Testing error handling with tracing
    print("\n❌ 4. Testing error handling with invalid email")
    invalid_cmd = ProcessUserRegistrationCommand(
        name="Invalid User",
        email="invalid-email"
    )
    
    error_result = app(
        invalid_cmd,
        RegistrationResult,
        correlation_id="reg-req-error-67890"
    )
    
    print(f"   Result: {error_result.message}")
    print(f"   Success: {error_result.success}")
    
    print("\n✅ POC completed successfully!")
    print("\nTrace data (if Jaeger is configured) can be viewed at:")
    print("   http://localhost:16686")
    print("\nWith Alloy and Grafana integration, traces would be visible in Grafana.")


if __name__ == "__main__":
    main()