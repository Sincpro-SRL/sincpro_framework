"""
Context implementation for Sincpro Framework

Provides automatic metadata propagation and scope management
with instance-based context storage for proper isolation.
"""

import traceback
from typing import Any, Dict, Optional, Type, TYPE_CHECKING
from datetime import datetime

from .sincpro_logger import logger

if TYPE_CHECKING:
    from .use_bus import UseFramework


class ContextObject:
    """
    Context object that provides access to framework context data
    """
    
    def __init__(self, context_data: Dict[str, Any]):
        self._data = context_data
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a specific value from the context"""
        return self._data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set a value in the context"""
        self._data[key] = value
    
    def update(self, data: Dict[str, Any]) -> None:
        """Update context with new data"""
        self._data.update(data)
    
    def all(self) -> Dict[str, Any]:
        """Get all context data"""
        return self._data.copy()
    
    def clear(self) -> None:
        """Clear all context data"""
        self._data.clear()


class ContextMixin:
    """
    Mixin to provide context access to Features and ApplicationServices
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_object: Optional[ContextObject] = None
    
    @property
    def context(self) -> ContextObject:
        """Get the current framework context object"""
        if self._context_object is None:
            # Return empty context if no context is set
            self._context_object = ContextObject({})
        return self._context_object
    
    def _set_context_object(self, context_obj: ContextObject) -> None:
        """Internal method to set the context object (used by framework)"""
        self._context_object = context_obj


class FrameworkContext:
    """
    Framework context manager that provides automatic metadata propagation
    and scope management with instance-based storage.
    """
    
    def __init__(self, framework_instance: 'UseFramework', context_attrs: Dict[str, Any]):
        self.framework = framework_instance
        self.context_attrs: Dict[str, Any] = context_attrs
        self._parent_context: Optional[Dict[str, Any]] = None
        self._is_entered = False

    def __enter__(self) -> 'UseFramework':
        """Enter the context manager and return framework instance with context"""
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")
        
        # Get current context from framework instance (if any) for nesting support
        current_context = self.framework._get_current_context()
        self._parent_context = current_context.copy() if current_context else {}
        
        # Merge parent context with new attributes (new attrs take precedence)
        merged_context = self._parent_context.copy()
        merged_context.update(self.context_attrs)
        
        # Set the new context on the framework instance
        self.framework._set_current_context(merged_context)
        self._is_entered = True
        
        # Log context establishment
        logger.debug(
            f"Framework context established with keys: {list(merged_context.keys())}"
        )
        
        return self.framework

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager and restore previous context"""
        if not self._is_entered:
            raise RuntimeError("Context manager was not entered")
        
        try:
            # Handle any exceptions that occurred within the context
            if exc_type is not None:
                self._handle_context_exception(exc_type, exc_val, exc_tb)
            
            # Restore parent context on the framework instance
            self.framework._set_current_context(self._parent_context or {})
            
            # Log context restoration
            logger.debug("Framework context restored to parent scope")
            
        finally:
            self._is_entered = False
            self._parent_context = None
        
        # Don't suppress exceptions unless explicitly handled
        return False

    def _handle_context_exception(self, exc_type: Type[Exception], exc_val: Exception, exc_tb) -> None:
        """Handle exceptions that occur within the context"""
        current_context = self.framework._get_current_context()
        
        # Enrich exception with context information
        context_info = {
            "context_data": current_context,
            "timestamp": datetime.now().isoformat(),
            "exception_type": exc_type.__name__,
            "context_stack_depth": len(str(exc_tb).split('\n')) if exc_tb else 0
        }
        
        # Log the error with context
        logger.error(
            f"Exception in framework context: {exc_type.__name__}: {exc_val}",
            extra={"context": context_info}
        )
        
        # Add context to exception if possible (some exceptions allow extra attributes)
        try:
            if hasattr(exc_val, 'context_info'):
                exc_val.context_info.update(context_info)
            else:
                exc_val.context_info = context_info
        except (AttributeError, TypeError):
            # Some built-in exceptions don't allow setting attributes
            pass