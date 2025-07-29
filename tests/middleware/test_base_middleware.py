import pytest
from sincpro_framework.middleware.base import BaseMiddleware, MiddlewareContext, MiddlewarePipeline


class TestMiddleware(BaseMiddleware):
    """Test middleware for testing pipeline functionality"""
    
    def __init__(self, name: str, priority: int = 100):
        super().__init__(name, priority=priority)
        self.pre_execute_called = False
        self.post_execute_called = False
        self.on_error_called = False
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        self.pre_execute_called = True
        context.add_metadata(f"{self.name}_pre", True)
        return context
    
    def post_execute(self, context: MiddlewareContext, result) -> any:
        self.post_execute_called = True
        context.add_metadata(f"{self.name}_post", True)
        return result
    
    def on_error(self, context: MiddlewareContext, error: Exception):
        self.on_error_called = True
        return None


def test_middleware_context():
    """Test middleware context functionality"""
    dto = {"test": "data"}
    context = MiddlewareContext(dto=dto)
    
    assert context.dto == dto
    assert isinstance(context.metadata, dict)
    assert isinstance(context.execution_id, str)
    assert isinstance(context.start_time, float)
    
    # Test metadata operations
    context.add_metadata("test_key", "test_value")
    assert context.get_metadata("test_key") == "test_value"
    assert context.get_metadata("nonexistent", "default") == "default"


def test_middleware_pipeline_empty():
    """Test pipeline with no middleware"""
    pipeline = MiddlewarePipeline()
    
    test_dto = {"test": "data"}
    executed = False
    
    def executor(dto, **kwargs):
        nonlocal executed
        executed = True
        return {"result": "success"}
    
    result = pipeline.execute(test_dto, executor)
    
    assert executed
    assert result == {"result": "success"}


def test_middleware_pipeline_single_middleware():
    """Test pipeline with single middleware"""
    pipeline = MiddlewarePipeline()
    middleware = TestMiddleware("test_middleware")
    pipeline.add_middleware(middleware)
    
    test_dto = {"test": "data"}
    
    def executor(dto, **kwargs):
        return {"result": "success"}
    
    result = pipeline.execute(test_dto, executor)
    
    assert middleware.pre_execute_called
    assert middleware.post_execute_called
    assert not middleware.on_error_called
    assert result == {"result": "success"}


def test_middleware_pipeline_multiple_middleware():
    """Test pipeline with multiple middleware in priority order"""
    pipeline = MiddlewarePipeline()
    
    # Add middleware with different priorities
    middleware1 = TestMiddleware("middleware1", priority=20)
    middleware2 = TestMiddleware("middleware2", priority=10)  # Higher priority (executed first)
    middleware3 = TestMiddleware("middleware3", priority=30)
    
    pipeline.add_middleware(middleware1)
    pipeline.add_middleware(middleware2)
    pipeline.add_middleware(middleware3)
    
    execution_order = []
    
    def custom_pre_execute(self, context):
        execution_order.append(f"{self.name}_pre")
        return TestMiddleware.pre_execute(self, context)
    
    def custom_post_execute(self, context, result):
        execution_order.append(f"{self.name}_post")
        return TestMiddleware.post_execute(self, context, result)
    
    # Monkey patch to track execution order
    for middleware in [middleware1, middleware2, middleware3]:
        middleware.pre_execute = custom_pre_execute.__get__(middleware, TestMiddleware)
        middleware.post_execute = custom_post_execute.__get__(middleware, TestMiddleware)
    
    test_dto = {"test": "data"}
    
    def executor(dto, **kwargs):
        return {"result": "success"}
    
    result = pipeline.execute(test_dto, executor)
    
    # Check that middleware executed in priority order (pre) and reverse order (post)
    expected_order = [
        "middleware2_pre",  # priority 10 (highest)
        "middleware1_pre",  # priority 20
        "middleware3_pre",  # priority 30
        "middleware3_post", # reverse order for post
        "middleware1_post",
        "middleware2_post"
    ]
    
    assert execution_order == expected_order
    assert result == {"result": "success"}


def test_middleware_pipeline_error_handling():
    """Test pipeline error handling"""
    pipeline = MiddlewarePipeline()
    middleware = TestMiddleware("test_middleware")
    pipeline.add_middleware(middleware)
    
    test_dto = {"test": "data"}
    
    def executor(dto, **kwargs):
        raise ValueError("Test error")
    
    with pytest.raises(ValueError, match="Test error"):
        pipeline.execute(test_dto, executor)
    
    assert middleware.pre_execute_called
    assert not middleware.post_execute_called
    assert middleware.on_error_called


def test_middleware_pipeline_error_recovery():
    """Test pipeline error recovery when middleware handles error"""
    pipeline = MiddlewarePipeline()
    
    class ErrorHandlingMiddleware(TestMiddleware):
        def on_error(self, context: MiddlewareContext, error: Exception):
            self.on_error_called = True
            return {"error_handled": True, "error_message": str(error)}
    
    middleware = ErrorHandlingMiddleware("error_handler")
    pipeline.add_middleware(middleware)
    
    test_dto = {"test": "data"}
    
    def executor(dto, **kwargs):
        raise ValueError("Test error")
    
    result = pipeline.execute(test_dto, executor)
    
    assert middleware.pre_execute_called
    assert not middleware.post_execute_called
    assert middleware.on_error_called
    assert result == {"error_handled": True, "error_message": "Test error"}


def test_middleware_disabled():
    """Test middleware can be disabled"""
    pipeline = MiddlewarePipeline()
    
    class DisabledMiddleware(TestMiddleware):
        def should_execute(self, context: MiddlewareContext) -> bool:
            return False
    
    middleware = DisabledMiddleware("disabled_middleware")
    pipeline.add_middleware(middleware)
    
    test_dto = {"test": "data"}
    
    def executor(dto, **kwargs):
        return {"result": "success"}
    
    result = pipeline.execute(test_dto, executor)
    
    assert not middleware.pre_execute_called
    assert not middleware.post_execute_called
    assert result == {"result": "success"}