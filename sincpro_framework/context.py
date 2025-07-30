"""
Context implementation for Sincpro Framework

Provides automatic metadata propagation and scope management
using Python's contextvars for thread-safe context storage.
"""

import sys
import traceback
from contextvars import ContextVar, copy_context
from typing import Any, Dict, Optional, Type, TYPE_CHECKING
from datetime import datetime

from .sincpro_logger import logger

if TYPE_CHECKING:
    from .use_bus import UseFramework

# Global context variable for framework context storage
_framework_context: ContextVar[Dict[str, Any]] = ContextVar(
    'framework_context', default={}
)


class FrameworkContext:
    """
    Framework context manager that provides automatic metadata propagation
    and scope management using contextvars.
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
        
        # Get current context (if any) for nesting support
        current_context = _framework_context.get({})
        self._parent_context = current_context.copy() if current_context else {}
        
        # Merge parent context with new attributes (new attrs take precedence)
        merged_context = self._parent_context.copy()
        merged_context.update(self.context_attrs)
        
        # Set the new context
        _framework_context.set(merged_context)
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
            
            # Restore parent context
            _framework_context.set(self._parent_context or {})
            
            # Log context restoration
            logger.debug("Framework context restored to parent scope")
            
        finally:
            self._is_entered = False
            self._parent_context = None
        
        # Don't suppress exceptions unless explicitly handled
        return False

    def _handle_context_exception(self, exc_type: Type[Exception], exc_val: Exception, exc_tb) -> None:
        """Handle exceptions that occur within the context"""
        current_context = _framework_context.get({})
        
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

    @classmethod
    def get_current_context(cls) -> Dict[str, Any]:
        """Get the current context data (class method for external access)"""
        return _framework_context.get({}).copy()

    @classmethod
    def copy_current_context(cls):
        """Copy the current context for use in async operations"""
        return copy_context()


# Convenience function to access current context from anywhere
def get_current_context() -> Dict[str, Any]:
    """Get the current framework context (convenience function)"""
    return FrameworkContext.get_current_context()


# Context-aware dependency injection mixin
class ContextAwareMixin:
    """Mixin to automatically inject current context into objects"""
    
    @property
    def context(self) -> Dict[str, Any]:
        """Get the current framework context"""
        return get_current_context()
    
    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Get a specific value from the current context"""
        return self.context.get(key, default)