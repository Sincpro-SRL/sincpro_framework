"""
Context Manager implementation for Sincpro Framework

Provides automatic metadata propagation, scope encapsulation, and uniform error handling
using Python's contextvars for thread-safe context storage.
"""

import sys
import traceback
from contextvars import ContextVar, copy_context
from typing import Any, Dict, List, Optional, Set, Type
from datetime import datetime
import uuid

from .sincpro_abstractions import DataTransferObject
from .sincpro_logger import logger


# Global context variable for framework context storage
_framework_context: ContextVar[Dict[str, Any]] = ContextVar(
    'framework_context', default={}
)


class ContextConfig:
    """Configuration for context manager behavior"""
    
    def __init__(
        self,
        default_attrs: Optional[Dict[str, Any]] = None,
        allowed_keys: Optional[Set[str]] = None,
        validate_types: bool = True,
        max_key_length: int = 100,
        max_value_length: int = 1000
    ):
        self.default_attrs = default_attrs or {}
        self.allowed_keys = allowed_keys  # None means all keys are allowed
        self.validate_types = validate_types
        self.max_key_length = max_key_length
        self.max_value_length = max_value_length


class ContextManager:
    """
    Framework context manager that provides automatic metadata propagation,
    scope encapsulation, and error handling using contextvars.
    """
    
    def __init__(self, config: Optional[ContextConfig] = None):
        self.config = config or ContextConfig()
        self.context_attrs: Dict[str, Any] = {}
        self._parent_context: Optional[Dict[str, Any]] = None
        self._is_entered = False

    def __enter__(self) -> 'ContextManager':
        """Enter the context manager and establish new context scope"""
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")
        
        # Get current context (if any) for nesting support
        current_context = _framework_context.get({})
        self._parent_context = current_context.copy() if current_context else {}
        
        # Merge parent context with new attributes (new attrs take precedence)
        merged_context = self._parent_context.copy()
        merged_context.update(self.config.default_attrs)
        merged_context.update(self.context_attrs)
        
        # Validate the merged context
        self._validate_context(merged_context)
        
        # Set the new context
        _framework_context.set(merged_context)
        self._is_entered = True
        
        # Log context establishment
        logger.debug(
            f"Framework context established with keys: {list(merged_context.keys())}"
        )
        
        return self

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

    def _validate_context(self, context_data: Dict[str, Any]) -> None:
        """Validate context data against configuration rules"""
        for key, value in context_data.items():
            # Check key length first
            if len(key) > self.config.max_key_length:
                raise ValueError(f"Context key '{key}' exceeds maximum length of {self.config.max_key_length}")
            
            # Check if key is allowed
            if self.config.allowed_keys and key not in self.config.allowed_keys:
                raise ValueError(f"Context key '{key}' is not in allowed keys: {self.config.allowed_keys}")
            
            # Check value length for strings
            if isinstance(value, str) and len(value) > self.config.max_value_length:
                raise ValueError(f"Context value for '{key}' exceeds maximum length of {self.config.max_value_length}")
            
            # Basic type validation
            if self.config.validate_types and not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError(f"Context value for '{key}' has unsupported type: {type(value)}")

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

    def set(self, key: str, value: Any) -> None:
        """Set a context value for the current scope"""
        if not self._is_entered:
            raise RuntimeError("Cannot set context value outside of context manager")
        
        current_context = _framework_context.get({})
        updated_context = current_context.copy()
        updated_context[key] = value
        
        self._validate_context({key: value})
        _framework_context.set(updated_context)
        
        logger.debug(f"Context key '{key}' updated in current scope")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a context value from the current scope"""
        current_context = _framework_context.get({})
        return current_context.get(key, default)

    def clear(self, key: str) -> bool:
        """Clear a context value from the current scope"""
        if not self._is_entered:
            raise RuntimeError("Cannot clear context value outside of context manager")
        
        current_context = _framework_context.get({})
        if key in current_context:
            updated_context = current_context.copy()
            del updated_context[key]
            _framework_context.set(updated_context)
            logger.debug(f"Context key '{key}' cleared from current scope")
            return True
        return False

    @classmethod
    def get_current_context(cls) -> Dict[str, Any]:
        """Get the current context data (class method for external access)"""
        return _framework_context.get({}).copy()

    @classmethod
    def copy_current_context(cls):
        """Copy the current context for use in async operations"""
        return copy_context()


def create_context_manager(
    context_attrs: Dict[str, Any],
    config: Optional[ContextConfig] = None
) -> ContextManager:
    """Factory function to create a context manager with specific attributes"""
    manager = ContextManager(config)
    manager.context_attrs = context_attrs.copy()
    return manager


# Convenience function to access current context from anywhere
def get_current_context() -> Dict[str, Any]:
    """Get the current framework context (convenience function)"""
    return ContextManager.get_current_context()


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