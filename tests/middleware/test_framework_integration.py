import pytest
from sincpro_framework import UseFramework, DataTransferObject, Feature
from sincpro_framework.middleware import ValidationMiddleware, ValidationRule, BaseMiddleware, MiddlewareContext


class TestIntegrationDTO(DataTransferObject):
    """Test DTO for integration testing"""
    amount: float
    user_id: str


class TestIntegrationResponse(DataTransferObject):
    """Test response DTO"""
    result: str
    processed_amount: float


class LoggingMiddleware(BaseMiddleware):
    """Simple logging middleware for testing"""
    
    def __init__(self):
        super().__init__("logging", priority=50)
        self.execution_log = []
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        self.execution_log.append(f"pre_execute: {type(context.dto).__name__}")
        context.add_metadata("logged", True)
        return context
    
    def post_execute(self, context: MiddlewareContext, result) -> any:
        self.execution_log.append(f"post_execute: {type(result).__name__}")
        return result


def test_framework_without_middleware():
    """Test framework works normally without middleware"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    # Test execution without middleware
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    result = framework(test_dto)
    
    assert isinstance(result, TestIntegrationResponse)
    assert result.result == "success"
    assert result.processed_amount == 200.0


def test_framework_with_middleware_enabled():
    """Test framework works with middleware enabled"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    # Add logging middleware
    logging_middleware = LoggingMiddleware()
    framework.add_middleware(logging_middleware)
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    # Test execution with middleware
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    result = framework(test_dto)
    
    assert isinstance(result, TestIntegrationResponse)
    assert result.result == "success"
    assert result.processed_amount == 200.0
    
    # Check that middleware was executed
    assert len(logging_middleware.execution_log) == 2
    assert "pre_execute: TestIntegrationDTO" in logging_middleware.execution_log
    assert "post_execute: TestIntegrationResponse" in logging_middleware.execution_log


def test_framework_with_middleware_disabled():
    """Test framework can disable middleware"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    # Add logging middleware
    logging_middleware = LoggingMiddleware()
    framework.add_middleware(logging_middleware)
    
    # Disable middleware
    framework.disable_middleware()
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    # Test execution with middleware disabled
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    result = framework(test_dto)
    
    assert isinstance(result, TestIntegrationResponse)
    assert result.result == "success"
    assert result.processed_amount == 200.0
    
    # Check that middleware was NOT executed
    assert len(logging_middleware.execution_log) == 0


def test_framework_with_validation_middleware():
    """Test framework with validation middleware"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    # Add validation middleware
    validation_middleware = ValidationMiddleware(strict_mode=True)
    
    def positive_amount(dto):
        return dto.amount > 0
    
    validation_middleware.add_validation_rule(
        "TestIntegrationDTO",
        ValidationRule("positive_amount", positive_amount, "Amount must be positive")
    )
    
    framework.add_middleware(validation_middleware)
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    # Test with valid data
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    result = framework(test_dto)
    
    assert isinstance(result, TestIntegrationResponse)
    assert result.result == "success"
    assert result.processed_amount == 200.0
    
    # Test with invalid data
    invalid_dto = TestIntegrationDTO(amount=-50.0, user_id="user123")
    
    with pytest.raises(Exception):  # ValidationError or similar
        framework(invalid_dto)


def test_framework_multiple_middleware_priority():
    """Test framework with multiple middleware in priority order"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    # Create middleware with different priorities
    class PriorityMiddleware(BaseMiddleware):
        def __init__(self, name: str, priority: int):
            super().__init__(name, priority=priority)
            self.execution_order = []
        
        def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
            self.execution_order.append(f"{self.name}_pre")
            return context
        
        def post_execute(self, context: MiddlewareContext, result) -> any:
            self.execution_order.append(f"{self.name}_post")
            return result
    
    middleware1 = PriorityMiddleware("middleware1", priority=30)
    middleware2 = PriorityMiddleware("middleware2", priority=10)  # Higher priority
    middleware3 = PriorityMiddleware("middleware3", priority=20)
    
    framework.add_middleware(middleware1)
    framework.add_middleware(middleware2)
    framework.add_middleware(middleware3)
    
    # Track global execution order
    global_execution_order = []
    
    def track_execution(middleware, phase):
        def wrapper(original_method):
            def tracked_method(*args, **kwargs):
                global_execution_order.append(f"{middleware.name}_{phase}")
                return original_method(*args, **kwargs)
            return tracked_method
        return wrapper
    
    # Patch all middleware to track execution
    for middleware in [middleware1, middleware2, middleware3]:
        original_pre = middleware.pre_execute
        original_post = middleware.post_execute
        
        middleware.pre_execute = track_execution(middleware, "pre")(original_pre)
        middleware.post_execute = track_execution(middleware, "post")(original_post)
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    # Execute
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    result = framework(test_dto)
    
    assert isinstance(result, TestIntegrationResponse)
    
    # Check execution order (priority 10, 20, 30 for pre, then reverse for post)
    expected_order = [
        "middleware2_pre",  # priority 10
        "middleware3_pre",  # priority 20
        "middleware1_pre",  # priority 30
        "middleware1_post", # reverse order
        "middleware3_post",
        "middleware2_post"
    ]
    
    assert global_execution_order == expected_order


def test_framework_middleware_error_handling():
    """Test framework middleware error handling"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    class ErrorMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__("error_middleware")
        
        def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
            raise ValueError("Middleware error")
        
        def post_execute(self, context: MiddlewareContext, result) -> any:
            return result
    
    framework.add_middleware(ErrorMiddleware())
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    
    with pytest.raises(ValueError, match="Middleware error"):
        framework(test_dto)


def test_framework_re_enable_middleware():
    """Test that middleware can be re-enabled after being disabled"""
    framework = UseFramework("test_framework", log_after_execution=False)
    
    logging_middleware = LoggingMiddleware()
    framework.add_middleware(logging_middleware)
    
    @framework.feature(TestIntegrationDTO)
    class TestFeature(Feature):
        def execute(self, dto: TestIntegrationDTO) -> TestIntegrationResponse:
            return TestIntegrationResponse(
                result="success",
                processed_amount=dto.amount * 2
            )
    
    test_dto = TestIntegrationDTO(amount=100.0, user_id="user123")
    
    # First execution with middleware enabled
    result1 = framework(test_dto)
    assert len(logging_middleware.execution_log) == 2
    
    # Disable middleware
    framework.disable_middleware()
    logging_middleware.execution_log.clear()
    
    result2 = framework(test_dto)
    assert len(logging_middleware.execution_log) == 0
    
    # Re-enable middleware
    framework.enable_middleware()
    
    result3 = framework(test_dto)
    assert len(logging_middleware.execution_log) == 2
    
    # All results should be the same
    assert result1.result == result2.result == result3.result == "success"