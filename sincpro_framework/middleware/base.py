from typing import Protocol, Any, List, Callable


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
        """Execute the middleware pipeline and then the main executor"""
        # Process DTO through all middleware
        processed_dto = dto
        for middleware in self.middlewares:
            processed_dto = middleware(processed_dto)
        
        # Execute main operation with processed DTO
        return executor(processed_dto, **kwargs)