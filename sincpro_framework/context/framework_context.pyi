from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from ..use_bus import UseFramework

class FrameworkContext:
    """
    Framework context manager that provides automatic metadata propagation
    and scope management with instance-based storage.

    When used with 'with' statement, returns the UseFramework instance
    with the context applied.
    """

    framework: "UseFramework"
    context: Dict[str, Any]
    parent_context: Dict[str, Any]

    def __init__(self, framework_instance: "UseFramework", context: Dict[str, Any]) -> None:
        """
        Initialize the context manager.

        Args:
            framework_instance: The UseFramework instance to manage context for
            context: The context dictionary to apply
        """
        ...

    def __enter__(self) -> "UseFramework":
        """
        Enter the context manager and return framework instance with context applied.

        Returns:
            The UseFramework instance with the merged context
        """
        ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """
        Exit the context manager and restore previous context.

        Returns:
            False to not suppress exceptions
        """
        ...
