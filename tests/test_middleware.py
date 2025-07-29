"""
Unit tests for the middleware system.

Tests cover:
1. Basic middleware functionality
2. DTO transformation between different pydantic models
3. Middleware pipeline execution order
4. Error handling in middleware
5. Integration with UseFramework
"""

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.middleware import MiddlewarePipeline


# Test DTOs - Original and Transformed
class OriginalDTO(DataTransferObject):
    name: str
    age: int


class TransformedDTO(DataTransferObject):
    name: str
    age: int
    processed: bool = True
    timestamp: str = "default"


class EnrichedDTO(DataTransferObject):
    name: str
    age: int
    processed: bool = True
    timestamp: str = "default"
    user_type: str = "standard"


class ValidationDTO(DataTransferObject):
    email: str
    amount: float


# Test middleware functions
def add_processed_flag(dto):
    """Simple middleware that adds a processed flag by creating new DTO"""
    if isinstance(dto, OriginalDTO):
        # Create a new DTO with the processed flag
        return TransformedDTO(
            name=dto.name,
            age=dto.age,
            processed=True,
            timestamp=getattr(dto, "timestamp", "default"),
        )
    elif hasattr(dto, "processed"):
        # Create new instance with processed flag set
        dto_dict = dto.model_dump()
        dto_dict["processed"] = True
        return type(dto)(**dto_dict)
    return dto


def add_timestamp(dto):
    """Middleware that adds timestamp by creating new DTO"""
    if hasattr(dto, "timestamp"):
        dto_dict = dto.model_dump()
        dto_dict["timestamp"] = "2024-01-01T00:00:00Z"
        return type(dto)(**dto_dict)
    return dto


def transform_dto_type(dto):
    """Middleware that transforms DTO to a different type"""
    if isinstance(dto, OriginalDTO):
        return TransformedDTO(
            name=dto.name, age=dto.age, processed=True, timestamp="2024-01-01T00:00:00Z"
        )
    return dto


def enrich_user_data(dto):
    """Middleware that enriches user data by creating new DTO"""
    if hasattr(dto, "age") and hasattr(dto, "user_type"):
        dto_dict = dto.model_dump()
        if dto.age >= 18:
            dto_dict["user_type"] = "adult"
        else:
            dto_dict["user_type"] = "minor"
        return type(dto)(**dto_dict)
    return dto


def validate_email(dto):
    """Middleware that validates email format"""
    if hasattr(dto, "email") and "@" not in dto.email:
        raise ValueError("Invalid email format")
    return dto


def validate_positive_amount(dto):
    """Middleware that validates amount is positive"""
    if hasattr(dto, "amount") and dto.amount <= 0:
        raise ValueError("Amount must be positive")
    return dto


class TestMiddlewarePipeline:
    """Test the MiddlewarePipeline class"""

    def test_empty_pipeline_execution(self):
        """Test that empty pipeline just calls executor"""
        pipeline = MiddlewarePipeline()

        def test_executor(dto, **kwargs):
            return f"executed_{dto}"

        result = pipeline.execute("test_dto", test_executor)
        assert result == "executed_test_dto"

    def test_single_middleware_execution(self):
        """Test pipeline with single middleware"""
        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(add_processed_flag)

        dto = OriginalDTO(name="John", age=25)

        def test_executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(dto, test_executor)
        assert isinstance(result, TransformedDTO)
        assert result.processed is True
        assert result.name == "John"
        assert result.age == 25

    def test_multiple_middleware_execution_order(self):
        """Test that middleware executes in the order they were added"""
        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(add_processed_flag)
        pipeline.add_middleware(add_timestamp)

        dto = OriginalDTO(name="John", age=25)

        def test_executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(dto, test_executor)
        assert isinstance(result, TransformedDTO)
        assert result.processed is True
        assert result.timestamp == "2024-01-01T00:00:00Z"

    def test_dto_transformation_between_types(self):
        """Test that middleware can transform between different DTO types"""
        pipeline = MiddlewarePipeline()

        def transform_to_enriched(dto):
            """Transform to EnrichedDTO which has user_type field"""
            if isinstance(dto, OriginalDTO):
                return EnrichedDTO(
                    name=dto.name,
                    age=dto.age,
                    processed=True,
                    timestamp="2024-01-01T00:00:00Z",
                    user_type="standard",
                )
            return dto

        pipeline.add_middleware(transform_to_enriched)
        pipeline.add_middleware(enrich_user_data)

        original_dto = OriginalDTO(name="Alice", age=30)

        def test_executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(original_dto, test_executor)

        # Should be transformed to EnrichedDTO
        assert isinstance(result, EnrichedDTO)
        assert result.name == "Alice"
        assert result.age == 30
        assert result.processed is True
        assert result.timestamp == "2024-01-01T00:00:00Z"
        assert result.user_type == "adult"

    def test_middleware_error_propagation(self):
        """Test that middleware errors are properly propagated"""
        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(validate_email)

        invalid_dto = ValidationDTO(email="invalid-email", amount=100.0)

        def test_executor(processed_dto, **kwargs):
            return processed_dto

        with pytest.raises(ValueError, match="Invalid email format"):
            pipeline.execute(invalid_dto, test_executor)

    def test_middleware_stops_on_error(self):
        """Test that middleware pipeline stops when one middleware raises an error"""
        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(validate_positive_amount)
        pipeline.add_middleware(add_processed_flag)  # This shouldn't execute

        invalid_dto = ValidationDTO(email="test@example.com", amount=-10.0)

        def test_executor(processed_dto, **kwargs):
            return processed_dto

        with pytest.raises(ValueError, match="Amount must be positive"):
            pipeline.execute(invalid_dto, test_executor)


class TestMiddlewareIntegration:
    """Test middleware integration with UseFramework"""

    def test_framework_with_middleware(self):
        """Test that UseFramework properly executes middleware"""
        framework = UseFramework("test_middleware", log_after_execution=False)
        framework.add_middleware(add_processed_flag)
        framework.add_middleware(add_timestamp)

        @framework.feature(
            TransformedDTO
        )  # Feature expects TransformedDTO after transformation
        class TestFeature(Feature):
            def execute(self, dto: TransformedDTO):
                return dto

        original_dto = OriginalDTO(name="Bob", age=25)
        result = framework(original_dto)

        assert isinstance(result, TransformedDTO)
        assert result.name == "Bob"
        assert result.age == 25
        assert result.processed is True
        assert result.timestamp == "2024-01-01T00:00:00Z"

    def test_framework_dto_transformation(self):
        """Test DTO transformation in framework execution"""
        framework = UseFramework("test_transform", log_after_execution=False)
        framework.add_middleware(transform_dto_type)

        @framework.feature(TransformedDTO)  # Feature expects TransformedDTO
        class TransformFeature(Feature):
            def execute(self, dto: TransformedDTO):
                # Verify we received the transformed DTO
                assert isinstance(dto, TransformedDTO)
                assert dto.processed is True
                return dto

        # Start with OriginalDTO, middleware should transform it
        original_dto = OriginalDTO(name="Charlie", age=35)
        result = framework(original_dto)

        assert isinstance(result, TransformedDTO)
        assert result.name == "Charlie"
        assert result.age == 35
        assert result.processed is True

    def test_framework_middleware_validation_error(self):
        """Test that validation errors in middleware are properly handled"""
        framework = UseFramework("test_validation", log_after_execution=False)
        framework.add_middleware(validate_email)
        framework.add_middleware(validate_positive_amount)

        @framework.feature(ValidationDTO)
        class ValidationFeature(Feature):
            def execute(self, dto: ValidationDTO):
                return dto

        # Test invalid email
        with pytest.raises(ValueError, match="Invalid email format"):
            invalid_dto = ValidationDTO(email="invalid", amount=100.0)
            framework(invalid_dto)

        # Test invalid amount
        with pytest.raises(ValueError, match="Amount must be positive"):
            invalid_dto = ValidationDTO(email="valid@example.com", amount=-50.0)
            framework(invalid_dto)

    def test_framework_complex_dto_transformation_chain(self):
        """Test complex DTO transformation through multiple middleware"""
        framework = UseFramework("test_complex", log_after_execution=False)

        # Chain of transformations
        def dto_to_enriched(dto):
            """Transform OriginalDTO -> TransformedDTO -> EnrichedDTO"""
            if isinstance(dto, OriginalDTO):
                return EnrichedDTO(
                    name=dto.name,
                    age=dto.age,
                    processed=True,
                    timestamp="2024-01-01T00:00:00Z",
                    user_type="standard",
                )
            return dto

        framework.add_middleware(dto_to_enriched)
        framework.add_middleware(enrich_user_data)  # Will update user_type based on age

        @framework.feature(EnrichedDTO)
        class ComplexFeature(Feature):
            def execute(self, dto: EnrichedDTO):
                return dto

        # Test with adult
        adult_dto = OriginalDTO(name="Diana", age=25)
        result = framework(adult_dto)

        assert isinstance(result, EnrichedDTO)
        assert result.name == "Diana"
        assert result.age == 25
        assert result.user_type == "adult"

        # Test with minor
        minor_dto = OriginalDTO(name="Emma", age=16)
        result = framework(minor_dto)

        assert isinstance(result, EnrichedDTO)
        assert result.name == "Emma"
        assert result.age == 16
        assert result.user_type == "minor"

    def test_framework_no_middleware(self):
        """Test that framework works normally without any middleware"""
        framework = UseFramework("test_no_middleware", log_after_execution=False)

        @framework.feature(OriginalDTO)
        class NoMiddlewareFeature(Feature):
            def execute(self, dto: OriginalDTO):
                return dto

        original_dto = OriginalDTO(name="Frank", age=40)
        result = framework(original_dto)

        assert result.name == "Frank"
        assert result.age == 40
        # No middleware applied, so no additional fields
        assert not hasattr(result, "processed")
        assert not hasattr(result, "timestamp")


class TestMiddlewareProtocol:
    """Test that the Middleware Protocol works correctly"""

    def test_callable_middleware(self):
        """Test that any callable can be used as middleware"""

        def simple_middleware(dto):
            """Create new DTO with simple flag"""
            if isinstance(dto, OriginalDTO):
                return TransformedDTO(
                    name=dto.name, age=dto.age, processed=False, timestamp="simple"
                )
            return dto

        # Should work as middleware
        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(simple_middleware)

        dto = OriginalDTO(name="Test", age=20)

        def executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(dto, executor)
        assert isinstance(result, TransformedDTO)
        assert result.timestamp == "simple"

    def test_lambda_middleware(self):
        """Test that lambda functions work as middleware"""
        lambda_middleware = lambda dto: (
            TransformedDTO(name=dto.name, age=dto.age, processed=False, timestamp="lambda")
            if isinstance(dto, OriginalDTO)
            else dto
        )

        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(lambda_middleware)

        dto = OriginalDTO(name="Lambda", age=30)

        def executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(dto, executor)
        assert isinstance(result, TransformedDTO)
        assert result.timestamp == "lambda"

    def test_class_based_middleware(self):
        """Test that callable classes work as middleware"""

        class ClassMiddleware:
            def __call__(self, dto):
                if isinstance(dto, OriginalDTO):
                    return TransformedDTO(
                        name=dto.name, age=dto.age, processed=False, timestamp="class"
                    )
                return dto

        pipeline = MiddlewarePipeline()
        pipeline.add_middleware(ClassMiddleware())

        dto = OriginalDTO(name="Class", age=25)

        def executor(processed_dto, **kwargs):
            return processed_dto

        result = pipeline.execute(dto, executor)
        assert isinstance(result, TransformedDTO)
        assert result.timestamp == "class"
