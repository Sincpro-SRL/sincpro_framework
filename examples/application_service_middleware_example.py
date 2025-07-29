#!/usr/bin/env python3
"""
Example showing middleware integration with ApplicationServices.

This demonstrates how middleware works with the full framework architecture:
- Features (atomic operations)
- ApplicationServices (orchestration)  
- Middleware (cross-cutting concerns)
"""

from sincpro_framework import UseFramework, Feature, ApplicationService, DataTransferObject
from sincpro_framework.middleware import ValidationMiddleware, ValidationRule


# DTOs for Features
class ValidateUserDTO(DataTransferObject):
    user_id: str
    email: str


class CreateAccountDTO(DataTransferObject):
    user_id: str
    initial_balance: float


class SendWelcomeEmailDTO(DataTransferObject):
    user_id: str
    email: str


# DTOs for Application Service
class UserRegistrationDTO(DataTransferObject):
    user_id: str
    email: str
    initial_balance: float


class RegistrationResultDTO(DataTransferObject):
    user_id: str
    account_created: bool
    email_sent: bool
    message: str


# Simple response DTOs
class ValidationResultDTO(DataTransferObject):
    valid: bool
    message: str


class AccountResultDTO(DataTransferObject):
    account_id: str
    balance: float


class EmailResultDTO(DataTransferObject):
    sent: bool
    message_id: str


# Create framework
registration_framework = UseFramework("registration_system", log_after_execution=False)

# Add validation middleware
validation_middleware = ValidationMiddleware(strict_mode=True)

def validate_email_format(dto) -> bool:
    """Business rule: Email must contain @ symbol"""
    return "@" in dto.email

def validate_positive_balance(dto) -> bool:
    """Business rule: Initial balance must be positive"""
    return dto.initial_balance > 0

def validate_user_id_format(dto) -> bool:
    """Business rule: User ID must be alphanumeric"""
    return dto.user_id.replace("_", "").isalnum()

# Add validation rules for all DTOs
for dto_name in ["ValidateUserDTO", "CreateAccountDTO", "SendWelcomeEmailDTO", "UserRegistrationDTO"]:
    if "email" in dto_name.lower() or dto_name == "ValidateUserDTO" or dto_name == "UserRegistrationDTO":
        validation_middleware.add_validation_rule(
            dto_name,
            ValidationRule("email_format", validate_email_format, "Email must contain @ symbol")
        )
    
    if "balance" in dto_name.lower() or dto_name == "UserRegistrationDTO":
        validation_middleware.add_validation_rule(
            dto_name,
            ValidationRule("positive_balance", validate_positive_balance, "Initial balance must be positive")
        )
    
    validation_middleware.add_validation_rule(
        dto_name,
        ValidationRule("user_id_format", validate_user_id_format, "User ID must be alphanumeric (underscores allowed)")
    )

registration_framework.add_middleware(validation_middleware)


# Features (atomic operations)
@registration_framework.feature(ValidateUserDTO)
class ValidateUserFeature(Feature):
    def execute(self, dto: ValidateUserDTO) -> ValidationResultDTO:
        print(f"    🔍 Validating user: {dto.user_id}")
        # Simulate user validation logic
        if dto.user_id == "duplicate_user":
            return ValidationResultDTO(valid=False, message="User already exists")
        return ValidationResultDTO(valid=True, message="User validation passed")


@registration_framework.feature(CreateAccountDTO)
class CreateAccountFeature(Feature):
    def execute(self, dto: CreateAccountDTO) -> AccountResultDTO:
        print(f"    💰 Creating account for user: {dto.user_id}")
        # Simulate account creation
        account_id = f"ACC_{dto.user_id}_{int(dto.initial_balance)}"
        return AccountResultDTO(account_id=account_id, balance=dto.initial_balance)


@registration_framework.feature(SendWelcomeEmailDTO)
class SendWelcomeEmailFeature(Feature):
    def execute(self, dto: SendWelcomeEmailDTO) -> EmailResultDTO:
        print(f"    📧 Sending welcome email to: {dto.email}")
        # Simulate email sending
        message_id = f"MSG_{dto.user_id}_WELCOME"
        return EmailResultDTO(sent=True, message_id=message_id)


# Application Service (orchestration)
@registration_framework.app_service(UserRegistrationDTO)
class UserRegistrationService(ApplicationService):
    def execute(self, dto: UserRegistrationDTO) -> RegistrationResultDTO:
        print(f"  🚀 Starting user registration for: {dto.user_id}")
        
        try:
            # Step 1: Validate user doesn't exist
            validation_result = self.feature_bus.execute(
                ValidateUserDTO(user_id=dto.user_id, email=dto.email)
            )
            
            if not validation_result.valid:
                return RegistrationResultDTO(
                    user_id=dto.user_id,
                    account_created=False,
                    email_sent=False,
                    message=f"Registration failed: {validation_result.message}"
                )
            
            # Step 2: Create account
            account_result = self.feature_bus.execute(
                CreateAccountDTO(user_id=dto.user_id, initial_balance=dto.initial_balance)
            )
            
            # Step 3: Send welcome email
            email_result = self.feature_bus.execute(
                SendWelcomeEmailDTO(user_id=dto.user_id, email=dto.email)
            )
            
            return RegistrationResultDTO(
                user_id=dto.user_id,
                account_created=True,
                email_sent=email_result.sent,
                message=f"Registration successful! Account: {account_result.account_id}"
            )
            
        except Exception as e:
            return RegistrationResultDTO(
                user_id=dto.user_id,
                account_created=False,
                email_sent=False,
                message=f"Registration failed: {str(e)}"
            )


def main():
    print("=== Middleware with Features and Application Services ===\n")
    
    # Test 1: Successful registration (Application Service)
    print("Test 1: Successful user registration")
    try:
        registration_request = UserRegistrationDTO(
            user_id="new_user_123",
            email="user@example.com",
            initial_balance=100.0
        )
        
        result = registration_framework(registration_request)
        print(f"✅ {result.message}")
        print(f"   Account created: {result.account_created}, Email sent: {result.email_sent}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 2: Validation failure - invalid email
    print("Test 2: Registration with invalid email")
    try:
        invalid_request = UserRegistrationDTO(
            user_id="user_456",
            email="invalid_email",  # Missing @
            initial_balance=50.0
        )
        
        result = registration_framework(invalid_request)
        print(f"Result: {result.message}")
    except Exception as e:
        print(f"❌ Middleware Validation Error: {e}")
    
    print()
    
    # Test 3: Validation failure - negative balance
    print("Test 3: Registration with negative balance")
    try:
        invalid_request = UserRegistrationDTO(
            user_id="user_789",
            email="user@example.com",
            initial_balance=-10.0  # Negative balance
        )
        
        result = registration_framework(invalid_request)
        print(f"Result: {result.message}")
    except Exception as e:
        print(f"❌ Middleware Validation Error: {e}")
    
    print()
    
    # Test 4: Individual feature execution
    print("Test 4: Direct feature execution")
    try:
        validate_request = ValidateUserDTO(
            user_id="test_user",
            email="test@example.com"
        )
        
        result = registration_framework(validate_request)
        print(f"✅ Feature result: {result.message} (Valid: {result.valid})")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 5: Business logic failure (duplicate user)
    print("Test 5: Registration with duplicate user")
    try:
        duplicate_request = UserRegistrationDTO(
            user_id="duplicate_user",  # This will fail business validation
            email="duplicate@example.com",
            initial_balance=100.0
        )
        
        result = registration_framework(duplicate_request)
        print(f"📋 {result.message}")
        print(f"   Account created: {result.account_created}, Email sent: {result.email_sent}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 6: Middleware disabled
    print("Test 6: Disable middleware and process invalid data")
    registration_framework.disable_middleware()
    
    try:
        invalid_request = UserRegistrationDTO(
            user_id="invalid@user!",  # Invalid characters
            email="no_at_symbol",     # Invalid email
            initial_balance=-50.0     # Negative balance
        )
        
        result = registration_framework(invalid_request)
        print(f"✅ Success (middleware disabled): {result.message}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    registration_framework.enable_middleware()
    print("    Middleware re-enabled")
    
    print("\n=== Architecture Summary ===")
    print("🏗️  Framework Architecture:")
    print("   • ApplicationService: UserRegistrationService (orchestrates multiple features)")
    print("   • Features: ValidateUserFeature, CreateAccountFeature, SendWelcomeEmailFeature")
    print("   • Middleware: ValidationMiddleware (validates all DTOs)")
    print("\n🔄 Execution Flow:")
    print("   1. Middleware validates DTO")
    print("   2. ApplicationService orchestrates business process")
    print("   3. Features execute atomic operations")
    print("   4. All DTOs are validated by middleware")
    print("\n✅ Benefits:")
    print("   • Cross-cutting validation applied everywhere")
    print("   • No code duplication in business logic")
    print("   • Centralized business rules")
    print("   • Easy to add new validation rules")


if __name__ == "__main__":
    main()