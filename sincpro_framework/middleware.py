from typing import Any, Callable, List, Protocol


class Middleware(Protocol):
    """Simple middleware protocol - just a callable that processes DTOs"""

    def __call__(self, dto: Any) -> Any:
        """
        Process the DTO and return the (possibly modified) DTO.
        Can raise exceptions if validation fails or requirements aren't met.

        Args:
            dto: The data transfer object to process

        Returns:
            The processed DTO (may be the same object or a modified version)

        Raises:
            Any exception if processing fails or validation doesn't pass
        """
        ...


class MiddlewarePipeline:
    """Simple pipeline that executes middleware functions in sequence"""

    def __init__(self):
        self.middlewares: List[Middleware] = []

    def add_middleware(self, middleware: Middleware):
        """Add middleware function to pipeline"""
        self.middlewares.append(middleware)

    def execute(self, dto: Any, executor: Callable, **kwargs) -> Any:
        """
        Execute the middleware pipeline and then the main executor.

        Preserves registry compatibility by monkey patching transformed DTOs
        to maintain the original DTO class for registry lookup.
        """
        # Store original class for registry compatibility
        original_dto_class = dto.__class__

        processed_dto = dto
        for middleware in self.middlewares:
            processed_dto = middleware(processed_dto)

        # If DTO type changed, monkey patch to preserve registry compatibility
        if processed_dto.__class__ != original_dto_class:
            processed_dto.__class__ = original_dto_class

        # Execute main operation with processed DTO
        return executor(processed_dto, **kwargs)
