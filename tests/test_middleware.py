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


class FullyEnrichedDTO(DataTransferObject):
    """DTO with all possible enriched fields"""

    name: str
    age: int
    processed: bool = True
    timestamp: str = "default"
    user_type: str = "standard"
    membership_status: str = "active"
    last_login: str = "never"


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
    """Test middleware integration with UseFramework using the monkey patching approach"""

    def test_framework_dto_transformation_with_registry_compatibility(self):
        """
        Test that middleware can transform DTOs while preserving registry compatibility.

        This test demonstrates the monkey patching approach where:
        1. Feature is registered with OriginalDTO
        2. Middleware transforms OriginalDTO -> TransformedDTO
        3. Framework automatically applies monkey patch to preserve registry compatibility
        4. Feature receives transformed data but appears as OriginalDTO to the registry
        """
        framework = UseFramework("test_registry_compat", log_after_execution=False)
        framework.add_middleware(transform_dto_type)

        # ✅ Register with ORIGINAL DTO (not transformed)
        @framework.feature(OriginalDTO)  # This is the key - register with original type
        class RegistryCompatibleFeature(Feature):
            def execute(self, dto: OriginalDTO):
                # Verify registry compatibility - dto appears as OriginalDTO
                assert isinstance(dto, OriginalDTO)
                assert dto.__class__.__name__ == "OriginalDTO"

                # But has enriched data from middleware transformation
                assert hasattr(dto, "processed")
                assert hasattr(dto, "timestamp")
                assert dto.processed is True
                assert dto.timestamp == "2024-01-01T00:00:00Z"

                return dto

        # Start with OriginalDTO, middleware transforms it, but registry still works
        original_dto = OriginalDTO(name="Registry", age=35)
        result = framework(original_dto)

        # Result should have monkey patched type
        assert isinstance(result, OriginalDTO)  # Appears as original due to monkey patch
        assert result.__class__.__name__ == "OriginalDTO"
        assert result.name == "Registry"
        assert result.age == 35

        # But has the enriched fields from transformation
        assert hasattr(result, "processed")
        assert hasattr(result, "timestamp")

    def test_framework_dto_aggregation_with_registry_preservation(self):
        """
        Test middleware that aggregates data from multiple sources while preserving registry compatibility.

        This demonstrates a common use case where middleware:
        1. Takes a simple DTO (e.g., UserIdDTO)
        2. Enriches it with data from external sources
        3. Creates an enriched DTO but preserves original type for registry
        """
        framework = UseFramework("test_aggregation", log_after_execution=False)

        # Middleware that simulates data aggregation
        def aggregate_user_data(dto):
            """Middleware that aggregates user data from 'external sources'"""
            if isinstance(dto, OriginalDTO):
                # Simulate fetching data from database, API, etc.
                aggregated_data = {
                    "name": dto.name,
                    "age": dto.age,
                    "processed": True,
                    "timestamp": "2024-01-01T12:00:00Z",
                    # Simulate enriched data from external sources
                    "user_type": "premium" if dto.age >= 25 else "standard",
                    "membership_status": "active",
                    "last_login": "2024-01-01T10:00:00Z",
                }

                # Create enriched DTO with aggregated data
                return FullyEnrichedDTO(**aggregated_data)
            return dto

        framework.add_middleware(aggregate_user_data)

        # ✅ Register with ORIGINAL DTO type for registry compatibility
        @framework.feature(OriginalDTO)
        class AggregatedDataFeature(Feature):
            def execute(self, dto: OriginalDTO):
                # Verify it appears as OriginalDTO to registry
                assert isinstance(dto, OriginalDTO)
                assert dto.__class__.__name__ == "OriginalDTO"

                # But has all the aggregated data available
                assert hasattr(dto, "processed")
                assert hasattr(dto, "user_type")
                assert hasattr(dto, "membership_status")
                assert hasattr(dto, "last_login")

                # Verify aggregated data values
                assert dto.processed is True
                assert dto.user_type in ["premium", "standard"]
                assert dto.membership_status == "active"
                assert dto.last_login == "2024-01-01T10:00:00Z"

                return dto

        # Test with premium user
        premium_user = OriginalDTO(name="Alice", age=30)
        result = framework(premium_user)

        assert isinstance(result, OriginalDTO)  # Registry compatibility preserved
        assert result.name == "Alice"
        assert result.age == 30
        assert hasattr(result, "user_type")
        assert result.user_type == "premium"  # Aggregated data accessible

        # Test with standard user
        standard_user = OriginalDTO(name="Bob", age=20)
        result = framework(standard_user)

        assert isinstance(result, OriginalDTO)
        assert result.name == "Bob"
        assert result.age == 20
        assert hasattr(result, "user_type")
        assert result.user_type == "standard"

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
