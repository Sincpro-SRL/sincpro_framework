#!/usr/bin/env python3
"""
Example demonstrating the new middleware system in Sincpro Framework.

This example shows how to:
1. Create and configure a framework instance
2. Add validation middleware with business rules
3. Execute features with middleware pipeline
4. Handle validation errors
"""

from sincpro_framework import UseFramework, Feature, DataTransferObject
from sincpro_framework.middleware import ValidationMiddleware, ValidationRule


# DTOs
class PaymentRequest(DataTransferObject):
    amount: float
    user_id: str
    description: str


class PaymentResponse(DataTransferObject):
    transaction_id: str
    status: str
    processed_amount: float


# Create framework instance
payment_framework = UseFramework("payment_system", log_after_execution=False)

# Add validation middleware with business rules
validation_middleware = ValidationMiddleware(strict_mode=True)

# Define business rules
def validate_positive_amount(dto: PaymentRequest) -> bool:
    """Business rule: Payment amount must be positive"""
    return dto.amount > 0


def validate_amount_limit(dto: PaymentRequest) -> bool:
    """Business rule: Payment amount must be under $10,000"""
    return dto.amount <= 10000


def validate_user_id(dto: PaymentRequest) -> bool:
    """Business rule: User ID must be provided"""
    return dto.user_id is not None and dto.user_id.strip() != ""


# Add validation rules
validation_middleware.add_validation_rule(
    "PaymentRequest",
    ValidationRule(
        name="positive_amount",
        validator=validate_positive_amount,
        error_message="Payment amount must be positive"
    )
)

validation_middleware.add_validation_rule(
    "PaymentRequest", 
    ValidationRule(
        name="amount_limit",
        validator=validate_amount_limit,
        error_message="Payment amount cannot exceed $10,000"
    )
)

validation_middleware.add_validation_rule(
    "PaymentRequest",
    ValidationRule(
        name="user_id_required",
        validator=validate_user_id,
        error_message="User ID is required"
    )
)

# Add middleware to framework
payment_framework.add_middleware(validation_middleware)


# Define feature
@payment_framework.feature(PaymentRequest)
class ProcessPaymentFeature(Feature):
    def execute(self, dto: PaymentRequest) -> PaymentResponse:
        # Simulate payment processing
        transaction_id = f"TXN_{dto.user_id}_{int(dto.amount * 100)}"
        
        return PaymentResponse(
            transaction_id=transaction_id,
            status="SUCCESS",
            processed_amount=dto.amount
        )


def main():
    print("=== Sincpro Framework Middleware Example ===\n")
    
    # Test 1: Valid payment
    print("Test 1: Valid payment request")
    try:
        valid_request = PaymentRequest(
            amount=100.50,
            user_id="USER123",
            description="Coffee purchase"
        )
        
        result = payment_framework(valid_request)
        print(f"✅ Success: {result.transaction_id} - Status: {result.status}")
        print(f"   Processed amount: ${result.processed_amount}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 2: Invalid payment - negative amount
    print("Test 2: Invalid payment request (negative amount)")
    try:
        invalid_request = PaymentRequest(
            amount=-50.0,
            user_id="USER123",
            description="Invalid payment"
        )
        
        result = payment_framework(invalid_request)
        print(f"✅ Success: {result.transaction_id}")
    except Exception as e:
        print(f"❌ Validation Error: {e}")
    
    print()
    
    # Test 3: Invalid payment - amount too high
    print("Test 3: Invalid payment request (amount too high)")
    try:
        invalid_request = PaymentRequest(
            amount=15000.0,
            user_id="USER123",
            description="Large payment"
        )
        
        result = payment_framework(invalid_request)
        print(f"✅ Success: {result.transaction_id}")
    except Exception as e:
        print(f"❌ Validation Error: {e}")
    
    print()
    
    # Test 4: Invalid payment - missing user ID
    print("Test 4: Invalid payment request (missing user ID)")
    try:
        invalid_request = PaymentRequest(
            amount=100.0,
            user_id="",
            description="No user payment"
        )
        
        result = payment_framework(invalid_request)
        print(f"✅ Success: {result.transaction_id}")
    except Exception as e:
        print(f"❌ Validation Error: {e}")
    
    print()
    
    # Test 5: Disable middleware and try invalid request
    print("Test 5: Disable middleware and process invalid request")
    payment_framework.disable_middleware()
    
    try:
        invalid_request = PaymentRequest(
            amount=-100.0,  # This would normally fail validation
            user_id="USER123",
            description="Bypassed validation"
        )
        
        result = payment_framework(invalid_request)
        print(f"✅ Success (middleware disabled): {result.transaction_id}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Re-enable middleware
    payment_framework.enable_middleware()
    print("   Middleware re-enabled")


if __name__ == "__main__":
    main()