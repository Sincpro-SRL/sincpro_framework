from typing import List, Callable, Any, Dict, Optional
from pydantic import BaseModel, ValidationError
from .base import BaseMiddleware, MiddlewareContext


class BusinessRuleValidationError(Exception):
    """Custom exception for business rule validation failures"""
    pass


class ValidationRule:
    """Single validation rule"""
    
    def __init__(self, name: str, validator: Callable[[Any], bool], 
                 error_message: str, severity: str = "error"):
        self.name = name
        self.validator = validator
        self.error_message = error_message
        self.severity = severity  # "error", "warning", "info"
    
    def validate(self, dto: Any) -> Optional[Dict[str, Any]]:
        """Execute validation rule"""
        try:
            is_valid = self.validator(dto)
            if not is_valid:
                return {
                    "rule": self.name,
                    "severity": self.severity,
                    "message": self.error_message,
                    "data": dto
                }
        except Exception as e:
            return {
                "rule": self.name,
                "severity": "error",
                "message": f"Validation rule '{self.name}' failed: {str(e)}",
                "data": dto
            }
        return None


class ValidationMiddleware(BaseMiddleware):
    """Advanced validation middleware with business rules"""
    
    def __init__(self, name: str = "validation", strict_mode: bool = True):
        super().__init__(name, priority=10)  # High priority
        self.validation_rules: Dict[str, List[ValidationRule]] = {}
        self.strict_mode = strict_mode
    
    def add_validation_rule(self, dto_type: str, rule: ValidationRule):
        """Add validation rule for specific DTO type"""
        if dto_type not in self.validation_rules:
            self.validation_rules[dto_type] = []
        self.validation_rules[dto_type].append(rule)
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        """Validate DTO before execution"""
        dto_type_name = type(context.dto).__name__
        
        # Pydantic validation (if applicable)
        if isinstance(context.dto, BaseModel):
            try:
                context.dto.model_validate(context.dto.model_dump())
            except ValidationError as e:
                raise e  # Re-raise original ValidationError
        
        # Business rule validation
        validation_errors = []
        validation_warnings = []
        
        rules = self.validation_rules.get(dto_type_name, [])
        for rule in rules:
            result = rule.validate(context.dto)
            if result:
                if result["severity"] == "error":
                    validation_errors.append(result)
                elif result["severity"] == "warning":
                    validation_warnings.append(result)
        
        # Handle validation results
        if validation_errors and self.strict_mode:
            raise BusinessRuleValidationError(f"Business rule validation failed: {validation_errors}")
        
        # Add validation info to context
        context.add_metadata("validation_errors", validation_errors)
        context.add_metadata("validation_warnings", validation_warnings)
        
        return context
    
    def post_execute(self, context: MiddlewareContext, result: Any) -> Any:
        """Post-execution validation if needed"""
        return result


# Helper functions for common validation patterns
def validate_positive_amount(dto) -> bool:
    """Business rule: Amount must be positive"""
    return hasattr(dto, 'amount') and dto.amount > 0


def validate_user_exists(dto) -> bool:
    """Business rule: User must exist"""
    return hasattr(dto, 'user_id') and dto.user_id is not None