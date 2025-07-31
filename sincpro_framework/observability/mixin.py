"""Observability mixin for Sincpro Framework buses."""

import time
from typing import Optional, Any, TYPE_CHECKING
from abc import abstractmethod

if TYPE_CHECKING:
    from ..sincpro_abstractions import TypeDTO, TypeDTOResponse


class ObservabilityMixin:
    """Mixin that provides observability functionality to bus classes."""

    def __init__(self):
        """Initialize observability-related attributes."""
        self.observability_enabled: bool = False
        self._tracer: Optional[Any] = None

    def enable_observability(self, tracer=None):
        """Enable observability for this bus."""
        self.observability_enabled = True
        self._tracer = tracer

    def _get_observability_config(self, dto_name: str) -> bool:
        """Get observability configuration for a DTO."""
        if hasattr(self, "observability_metadata"):
            metadata = getattr(self, "observability_metadata", {})
            if hasattr(metadata, "kwargs"):
                metadata = metadata.kwargs
            return metadata.get(dto_name, {}).get("observability", False)
        return False

    def _should_trace(self, dto_name: str) -> bool:
        """Determine if this execution should be traced."""
        return (
            self.observability_enabled
            and self._tracer
            and self._get_observability_config(dto_name)
        )

    def _execute(self, dto: "TypeDTO", dto_name: str) -> "TypeDTOResponse | None":
        """Template method for executing business logic with optional observability."""
        if self._should_trace(dto_name):
            return self._execute_with_observability(dto, dto_name)
        else:
            return self._execute_business_logic_with_logging(dto, dto_name)

    def _execute_with_observability(
        self, dto: "TypeDTO", dto_name: str
    ) -> "TypeDTOResponse | None":
        """Execute with observability tracking."""
        # Get bus type for tracing
        bus_type = getattr(self, "_get_bus_type", lambda: "unknown")()
        
        # Generate trace/request ID if needed
        try:
            from .correlation import correlation_manager

            correlation_manager.get_or_create_correlation_id()
        except ImportError:
            pass  # Graceful degradation

        # Start span for execution
        span = self._tracer.start_span(f"{bus_type}.execute.{dto_name}")

        try:
            # Add span attributes
            self._tracer.set_attributes(
                span,
                {
                    f"{bus_type}.name": dto_name,
                    "bus.type": bus_type,
                    "observability.enabled": True,
                },
            )

            # Track execution time
            start_time = time.time()

            # Execute the actual business logic
            response = self._execute_business_logic(dto)

            # Record successful execution
            duration = time.time() - start_time
            self._tracer.set_attributes(
                span,
                {"execution.duration_ms": duration * 1000, "execution.status": "success"},
            )

            # Log response if available
            self._log_response(response)

            return response

        except Exception as error:
            # Record error in span
            duration = time.time() - start_time
            self._tracer.record_exception(span, error)
            self._tracer.set_attributes(
                span,
                {
                    "execution.duration_ms": duration * 1000,
                    "execution.status": "error",
                    "error.type": type(error).__name__,
                },
            )

            # Handle error if handler exists
            if hasattr(self, "handle_error") and self.handle_error:
                return self.handle_error(error)
            raise error
        finally:
            span.end()

    def _execute_business_logic_with_logging(
        self, dto: "TypeDTO", dto_name: str
    ) -> "TypeDTOResponse | None":
        """Execute business logic with standard logging (no observability)."""
        try:
            response = self._execute_business_logic(dto)

            # Log response if available
            self._log_response(response)

            return response

        except Exception as error:
            # Handle error if handler exists
            if hasattr(self, "handle_error") and self.handle_error:
                return self.handle_error(error)
            raise error

    def _log_response(self, response: "TypeDTOResponse | None") -> None:
        """Log the response if available."""
        if response and hasattr(self, "logger"):
            response_type = getattr(self, "_get_response_log_type", lambda: "Service")()
            self.logger.debug(
                f"{response_type} response {response.__class__.__name__}({response})"
            )

    def _get_bus_type(self) -> str:
        """Get the bus type for tracing. Override in subclasses."""
        return "unknown"

    @abstractmethod
    def _execute_business_logic(self, dto: "TypeDTO") -> "TypeDTOResponse | None":
        """Execute the actual business logic. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _execute_business_logic method")
