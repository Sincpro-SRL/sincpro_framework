import pytest
from pydantic import ValidationError
from sincpro_framework.middleware.validation import ValidationMiddleware, ValidationRule, BusinessRuleValidationError
from sincpro_framework.middleware.base import MiddlewareContext
from sincpro_framework import DataTransferObject


class TestDTO(DataTransferObject):
    """Test DTO for validation"""
    amount: float
    user_id: str


def test_validation_rule_success():
    """Test validation rule that passes"""
    def validator(dto):
        return dto.amount > 0
    
    rule = ValidationRule(
        name="positive_amount",
        validator=validator,
        error_message="Amount must be positive"
    )
    
    test_dto = TestDTO(amount=100.0, user_id="user123")
    result = rule.validate(test_dto)
    
    assert result is None  # No validation error


def test_validation_rule_failure():
    """Test validation rule that fails"""
    def validator(dto):
        return dto.amount > 0
    
    rule = ValidationRule(
        name="positive_amount",
        validator=validator,
        error_message="Amount must be positive"
    )
    
    test_dto = TestDTO(amount=-50.0, user_id="user123")
    result = rule.validate(test_dto)
    
    assert result is not None
    assert result["rule"] == "positive_amount"
    assert result["severity"] == "error"
    assert result["message"] == "Amount must be positive"


def test_validation_rule_exception():
    """Test validation rule that raises exception"""
    def validator(dto):
        raise AttributeError("Invalid attribute access")
    
    rule = ValidationRule(
        name="failing_rule",
        validator=validator,
        error_message="Should not see this"
    )
    
    test_dto = TestDTO(amount=100.0, user_id="user123")
    result = rule.validate(test_dto)
    
    assert result is not None
    assert result["rule"] == "failing_rule"
    assert result["severity"] == "error"
    assert "Invalid attribute access" in result["message"]


def test_validation_middleware_no_rules():
    """Test validation middleware with no rules"""
    middleware = ValidationMiddleware()
    test_dto = TestDTO(amount=100.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("validation_errors") == []
    assert context.get_metadata("validation_warnings") == []


def test_validation_middleware_passing_rules():
    """Test validation middleware with passing rules"""
    middleware = ValidationMiddleware()
    
    # Add validation rules
    def positive_amount(dto):
        return dto.amount > 0
    
    def user_exists(dto):
        return dto.user_id is not None
    
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("positive_amount", positive_amount, "Amount must be positive")
    )
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("user_exists", user_exists, "User must exist")
    )
    
    test_dto = TestDTO(amount=100.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("validation_errors") == []
    assert context.get_metadata("validation_warnings") == []


def test_validation_middleware_failing_rules_strict():
    """Test validation middleware with failing rules in strict mode"""
    middleware = ValidationMiddleware(strict_mode=True)
    
    def negative_amount(dto):
        return dto.amount < 0  # This will fail for positive amounts
    
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("negative_amount", negative_amount, "Amount must be negative")
    )
    
    test_dto = TestDTO(amount=100.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    with pytest.raises(BusinessRuleValidationError, match="Business rule validation failed"):
        middleware.pre_execute(context)


def test_validation_middleware_failing_rules_non_strict():
    """Test validation middleware with failing rules in non-strict mode"""
    middleware = ValidationMiddleware(strict_mode=False)
    
    def negative_amount(dto):
        return dto.amount < 0
    
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("negative_amount", negative_amount, "Amount must be negative")
    )
    
    test_dto = TestDTO(amount=100.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    validation_errors = context.get_metadata("validation_errors")
    assert len(validation_errors) == 1
    assert validation_errors[0]["rule"] == "negative_amount"


def test_validation_middleware_warnings():
    """Test validation middleware with warning-level rules"""
    middleware = ValidationMiddleware(strict_mode=True)
    
    def large_amount(dto):
        return dto.amount <= 1000
    
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule(
            "large_amount", 
            large_amount, 
            "Large amount detected", 
            severity="warning"
        )
    )
    
    test_dto = TestDTO(amount=5000.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("validation_errors") == []
    validation_warnings = context.get_metadata("validation_warnings")
    assert len(validation_warnings) == 1
    assert validation_warnings[0]["rule"] == "large_amount"
    assert validation_warnings[0]["severity"] == "warning"


def test_validation_middleware_mixed_results():
    """Test validation middleware with mixed error and warning results"""
    middleware = ValidationMiddleware(strict_mode=False)
    
    def positive_amount(dto):
        return dto.amount > 0
    
    def small_amount(dto):
        return dto.amount < 1000
    
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("positive_amount", positive_amount, "Amount must be positive", "error")
    )
    middleware.add_validation_rule(
        "TestDTO",
        ValidationRule("small_amount", small_amount, "Large amount warning", "warning")
    )
    
    test_dto = TestDTO(amount=-100.0, user_id="user123")  # Negative amount
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    validation_errors = context.get_metadata("validation_errors")
    validation_warnings = context.get_metadata("validation_warnings")
    
    assert len(validation_errors) == 1
    assert validation_errors[0]["rule"] == "positive_amount"
    assert len(validation_warnings) == 0  # Won't get warning since amount is negative


def test_validation_middleware_pydantic_validation():
    """Test that Pydantic validation still works"""
    class StrictDTO(DataTransferObject):
        amount: int  # Only integers allowed
        user_id: str
    
    middleware = ValidationMiddleware()
    
    # This should pass Pydantic validation
    test_dto = StrictDTO(amount=100, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert result_context == context


def test_validation_middleware_post_execute():
    """Test post_execute method (should pass through result)"""
    middleware = ValidationMiddleware()
    test_dto = TestDTO(amount=100.0, user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result = {"status": "success"}
    post_result = middleware.post_execute(context, result)
    
    assert post_result == result