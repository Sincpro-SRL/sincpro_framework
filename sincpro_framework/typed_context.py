"""
Typed context support for Sincpro Framework.

This module provides type-safe context access for Features and ApplicationServices.
"""

from typing import Any, Dict, Generic, TypeVar, Union, get_type_hints
from typing_extensions import TypedDict

# Type variable for context typing
TContext = TypeVar("TContext", bound=Dict[str, Any])


class TypedContext(Generic[TContext]):
    """
    A wrapper around the context dictionary that provides type-safe access.
    
    This class wraps the standard context dict and provides typed access methods
    while maintaining full backward compatibility with the existing dict interface.
    """

    def __init__(self, context: Dict[str, Any]):
        """Initialize the typed context wrapper.
        
        Args:
            context: The underlying context dictionary
        """
        self._context = context

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the context with optional default.
        
        Args:
            key: The context key to retrieve
            default: Default value if key not found
            
        Returns:
            The value for the key or the default
        """
        return self._context.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Get a value from the context using bracket notation."""
        return self._context[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a value in the context using bracket notation."""
        self._context[key] = value

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the context."""
        return key in self._context

    def keys(self):
        """Return the context keys."""
        return self._context.keys()

    def values(self):
        """Return the context values."""
        return self._context.values()

    def items(self):
        """Return the context items."""
        return self._context.items()

    def copy(self) -> Dict[str, Any]:
        """Return a copy of the underlying context dictionary."""
        return self._context.copy()

    def update(self, other: Dict[str, Any]) -> None:
        """Update the context with another dictionary."""
        self._context.update(other)

    def __repr__(self) -> str:
        """Return string representation of the context."""
        return f"TypedContext({self._context})"

    def __len__(self) -> int:
        """Return the number of items in the context."""
        return len(self._context)

    @property
    def raw_dict(self) -> Dict[str, Any]:
        """Access the underlying dictionary directly.
        
        This provides access to the raw dict for cases where full dict
        functionality is needed.
        """
        return self._context


class ContextTypeMixin:
    """
    Mixin class to provide typed context access to Features and ApplicationServices.
    
    This mixin allows users to define their context type and get type-safe access
    while maintaining the flexibility of the original dict-based context.
    
    Example usage:
        class MyContextKeys(TypedDict, total=False):
            TOKEN: NotRequired[str]
            USER_ID: NotRequired[str]
            
        class MyContextType(ContextTypeMixin):
            context: MyContextKeys
            
        class MyFeature(Feature, MyContextType):
            def execute(self, dto):
                token = self.context.get("TOKEN")  # Type-safe access
                return SomeResponse(token=token)
    """

    context: Union[Dict[str, Any], TypedContext]

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """
        Type-safe method to get a context value.
        
        This method provides a convenient way to access context values
        with proper type hints when used with typed context mixins.
        
        Args:
            key: The context key to retrieve
            default: Default value if key not found
            
        Returns:
            The value for the key or the default
        """
        if hasattr(self, 'context') and self.context:
            return self.context.get(key, default)
        return default

    def has_context_key(self, key: str) -> bool:
        """
        Check if a context key exists.
        
        Args:
            key: The context key to check
            
        Returns:
            True if the key exists, False otherwise
        """
        if hasattr(self, 'context') and self.context:
            return key in self.context
        return False


# Utility function to create typed context instances
def create_typed_context(context_dict: Dict[str, Any]) -> TypedContext:
    """
    Create a TypedContext instance from a dictionary.
    
    Args:
        context_dict: The context dictionary to wrap
        
    Returns:
        A TypedContext instance wrapping the provided dictionary
    """
    return TypedContext(context_dict)