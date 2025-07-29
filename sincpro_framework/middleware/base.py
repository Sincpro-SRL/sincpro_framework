from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class MiddlewareContext:
    """Context passed through middleware pipeline"""
    dto: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    user_context: Optional[Dict[str, Any]] = None
    
    def add_metadata(self, key: str, value: Any):
        """Add metadata to context"""
        self.metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata from context"""
        return self.metadata.get(key, default)


class BaseMiddleware(ABC):
    """Base class for all middleware implementations"""
    
    def __init__(self, name: str, enabled: bool = True, priority: int = 100):
        self.name = name
        self.enabled = enabled
        self.priority = priority  # Lower = higher priority
    
    @abstractmethod
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        """Execute before the main operation"""
        pass
    
    @abstractmethod
    def post_execute(self, context: MiddlewareContext, result: Any) -> Any:
        """Execute after the main operation"""
        pass
    
    def on_error(self, context: MiddlewareContext, error: Exception) -> Optional[Any]:
        """Handle errors during execution"""
        return None  # Re-raise by default
    
    def should_execute(self, context: MiddlewareContext) -> bool:
        """Determine if middleware should execute for this context"""
        return self.enabled


class MiddlewarePipeline:
    """Manages and executes middleware chain"""
    
    def __init__(self):
        self.middlewares: List[BaseMiddleware] = []
        self._sorted = False
    
    def add_middleware(self, middleware: BaseMiddleware):
        """Add middleware to pipeline"""
        self.middlewares.append(middleware)
        self._sorted = False
    
    def _sort_middlewares(self):
        """Sort middlewares by priority"""
        if not self._sorted:
            self.middlewares.sort(key=lambda m: m.priority)
            self._sorted = True
    
    def execute(self, dto: Any, executor: Callable, **kwargs) -> Any:
        """Execute the complete middleware pipeline"""
        self._sort_middlewares()
        
        # Create context
        context = MiddlewareContext(dto=dto)
        
        try:
            # Pre-execution phase
            for middleware in self.middlewares:
                if middleware.should_execute(context):
                    context = middleware.pre_execute(context)
            
            # Main execution
            result = executor(context.dto, **kwargs)
            
            # Post-execution phase (reverse order)
            for middleware in reversed(self.middlewares):
                if middleware.should_execute(context):
                    result = middleware.post_execute(context, result)
            
            return result
            
        except Exception as e:
            # Error handling phase
            for middleware in reversed(self.middlewares):
                if middleware.should_execute(context):
                    handled_result = middleware.on_error(context, e)
                    if handled_result is not None:
                        return handled_result
            raise