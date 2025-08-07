"""
Simple example demonstrating typed context support in Sincpro Framework.

This shows the simple approach without mixins or helper methods - just typing.
"""

from typing_extensions import NotRequired, TypedDict

from sincpro_framework import DataTransferObject, Feature, UseFramework


# Example 1: Define your context structure using TypedDict
class MyContextKeys(TypedDict, total=False):
    """Known context keys with their types"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]


# Example 2: Initialize framework with context type
framework: UseFramework[MyContextKeys] = UseFramework()


# Example 3: Define DTOs
class ProcessRequestDTO(DataTransferObject):
    """Request DTO for features"""
    operation: str
    data: str


class ProcessResponseDTO(DataTransferObject):
    """Response DTO for features"""
    result: str
    token: str = ""
    user_id: str = ""


# Example 4: Features automatically get typed context
@framework.feature(ProcessRequestDTO)
class ProcessFeature(Feature[ProcessRequestDTO, ProcessResponseDTO]):
    """Example feature with typed context access"""

    def execute(self, dto: ProcessRequestDTO) -> ProcessResponseDTO:
        # The context is typed as MyContextKeys but still works as dict[str, Any]
        token = self.context.get("TOKEN")  # Type checker knows this is str | None
        user_id = self.context.get("USER_ID")  # Type checker knows this is str | None
        
        # Still works with any key for backward compatibility
        correlation_id = self.context.get("CORRELATION_ID", "default")
        
        # Use the typed values
        return ProcessResponseDTO(
            result=f"Processed {dto.operation} for {dto.data}",
            token=token or "no-token",
            user_id=user_id or "anonymous"
        )


def main():
    """Example usage"""
    # Build framework
    framework.build()
    
    # Use with context
    with framework.with_context({
        "TOKEN": "abc123",
        "USER_ID": "user456",
        "CORRELATION_ID": "req789"
    }) as fw:
        # Execute feature
        request = ProcessRequestDTO(operation="create", data="example")
        response = fw.feature_bus.execute(request, ProcessResponseDTO)
        print(f"Response: {response}")


if __name__ == "__main__":
    main()