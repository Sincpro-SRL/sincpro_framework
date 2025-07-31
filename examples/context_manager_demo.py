"""
Example demonstrating Context Manager functionality in Sincpro Framework

This example shows how to use the new context manager to:
1. Propagate metadata across Features and ApplicationServices
2. Use nested contexts with override support
3. Access context data within handlers
4. Handle errors with context enrichment
"""

from sincpro_framework import UseFramework, DataTransferObject, Feature, ApplicationService


# Define DTOs
class ProcessOrderDTO(DataTransferObject):
    """DTO for processing an order"""
    order_id: str
    customer_id: str
    amount: float


class OrderResponseDTO(DataTransferObject):
    """Response DTO for order processing"""
    order_id: str
    status: str
    message: str
    context_info: dict = {}


class PaymentDTO(DataTransferObject):
    """DTO for payment processing"""
    amount: float
    customer_id: str


class PaymentResponseDTO(DataTransferObject):
    """Response DTO for payment"""
    transaction_id: str
    status: str
    context_info: dict = {}


# Define Features that use context
class PaymentProcessingFeature(Feature):
    """Feature that processes payments and uses context"""
    
    def execute(self, dto: PaymentDTO) -> PaymentResponseDTO:
        # Access context information
        correlation_id = self.get_context_value("correlation_id", "unknown")
        user_id = self.get_context_value("user.id", "system")
        service_name = self.get_context_value("service.name", "payment-service")
        
        # Simulate payment processing
        transaction_id = f"txn_{correlation_id}_{dto.customer_id}"
        
        return PaymentResponseDTO(
            transaction_id=transaction_id,
            status="success",
            context_info={
                "correlation_id": correlation_id,
                "processed_by": user_id,
                "service": service_name,
                "full_context": self.context
            }
        )


# Define Application Service that orchestrates Features
class OrderProcessingService(ApplicationService):
    """Application Service that orchestrates order processing"""
    
    def execute(self, dto: ProcessOrderDTO) -> OrderResponseDTO:
        # Access context in the application service
        correlation_id = self.get_context_value("correlation_id", "unknown")
        session_id = self.get_context_value("session.id", "no-session")
        
        # Execute payment feature through the feature bus
        payment_dto = PaymentDTO(amount=dto.amount, customer_id=dto.customer_id)
        payment_result = self.feature_bus.execute(payment_dto)
        
        # Process the order
        status = "completed" if payment_result.status == "success" else "failed"
        
        return OrderResponseDTO(
            order_id=dto.order_id,
            status=status,
            message=f"Order {dto.order_id} {status}",
            context_info={
                "service_correlation_id": correlation_id,
                "service_session_id": session_id,
                "payment_context": payment_result.context_info,
                "service_context": self.context
            }
        )


def main():
    """Demonstrate context manager functionality"""
    
    # Initialize framework
    framework = UseFramework("order-service")
    
    # Add some dependencies
    framework.add_dependency("payment_gateway", "mock_payment_gateway")
    framework.add_dependency("database", "mock_database")
    
    # Register Features and ApplicationServices
    @framework.feature(PaymentDTO)
    class PaymentFeature(PaymentProcessingFeature):
        pass
    
    @framework.app_service(ProcessOrderDTO)
    class OrderService(OrderProcessingService):
        pass
    
    print("=== Context Manager Demo ===\n")
    
    # Example 1: Execute without context
    print("1. Executing without context:")
    order_dto = ProcessOrderDTO(order_id="ORD-001", customer_id="CUST-123", amount=99.99)
    result_no_context = framework(order_dto)
    print(f"   Result: {result_no_context.status}")
    print(f"   Service correlation_id: {result_no_context.context_info['service_correlation_id']}")
    print(f"   Payment correlation_id: {result_no_context.context_info['payment_context']['correlation_id']}")
    print()
    
    # Example 2: Execute with context
    print("2. Executing with context:")
    with framework.context({
        "correlation_id": "CORR-12345",
        "user.id": "admin@company.com",
        "session.id": "SESSION-ABC123",
        "environment": "production"
    }) as app_with_context:
        order_dto = ProcessOrderDTO(order_id="ORD-002", customer_id="CUST-456", amount=149.99)
        result_with_context = app_with_context(order_dto)
        print(f"   Result: {result_with_context.status}")
        print(f"   Service correlation_id: {result_with_context.context_info['service_correlation_id']}")
        print(f"   Payment correlation_id: {result_with_context.context_info['payment_context']['correlation_id']}")
        print(f"   User: {result_with_context.context_info['payment_context']['processed_by']}")
        print(f"   Environment: {result_with_context.context_info['service_context']['environment']}")
    print()
    
    # Example 3: Nested contexts with overrides
    print("3. Nested contexts with overrides:")
    with framework.context({
        "correlation_id": "OUTER-CONTEXT",
        "user.id": "manager@company.com",
        "environment": "staging"
    }) as outer_app:
        print("   Outer context established...")
        
        with outer_app.context({
            "correlation_id": "INNER-CONTEXT",  # Override
            "feature_flag": "beta_payments"     # New attribute
        }) as inner_app:
            print("   Inner context with overrides...")
            order_dto = ProcessOrderDTO(order_id="ORD-003", customer_id="CUST-789", amount=199.99)
            result_nested = inner_app(order_dto)
            print(f"   Result: {result_nested.status}")
            print(f"   Correlation ID (overridden): {result_nested.context_info['service_correlation_id']}")
            print(f"   User (inherited): {result_nested.context_info['payment_context']['processed_by']}")
            print(f"   Environment (inherited): {result_nested.context_info['service_context']['environment']}")
    print()
    
    # Example 4: Error handling with context
    print("4. Error handling with context enrichment:")
    
    # Create a separate framework instance for the error test to avoid DTO conflicts
    error_framework = UseFramework("error-test-service")
    
    @error_framework.feature(PaymentDTO)
    class FailingPaymentFeature(Feature):
        def execute(self, dto: PaymentDTO) -> PaymentResponseDTO:
            raise ValueError("Payment gateway unavailable")
    
    try:
        with error_framework.context({
            "correlation_id": "ERROR-TEST",
            "user.id": "test@company.com"
        }) as error_app:
            payment_dto = PaymentDTO(amount=50.0, customer_id="CUST-ERROR")
            error_app(payment_dto)
    except ValueError as e:
        print(f"   Caught exception: {e}")
        if hasattr(e, 'context_info'):
            print(f"   Context correlation_id: {e.context_info['context_data']['correlation_id']}")
            print(f"   Context user: {e.context_info['context_data']['user.id']}")
            print(f"   Exception timestamp: {e.context_info['timestamp']}")
        else:
            print("   Exception was not enriched with context (this is expected for some built-in exceptions)")
    print()
    
    print("=== Demo completed successfully! ===")


if __name__ == "__main__":
    main()