"""Request correlation functionality for Sincpro Framework."""

import uuid
import contextvars
from typing import Optional, Dict, Any


# Context variable for correlation ID
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    'correlation_id', default=None
)

# Context variable for trace ID
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    'trace_id', default=None
)


class CorrelationManager:
    """Manages request correlation across the framework."""
    
    def __init__(self):
        self.correlation_header = "X-Correlation-ID"
        self.trace_header = "X-Trace-ID"
        self.request_id_header = "X-Request-ID"
    
    def generate_correlation_id(self) -> str:
        """Generate new correlation ID."""
        return str(uuid.uuid4())
    
    def set_correlation_id(self, correlation_id: str):
        """Set correlation ID for current context."""
        correlation_id_var.set(correlation_id)
    
    def get_correlation_id(self) -> Optional[str]:
        """Get correlation ID from current context."""
        return correlation_id_var.get()
    
    def get_or_create_correlation_id(self) -> str:
        """Get existing or create new correlation ID."""
        correlation_id = self.get_correlation_id()
        if not correlation_id:
            correlation_id = self.generate_correlation_id()
            self.set_correlation_id(correlation_id)
        return correlation_id
    
    def set_trace_id(self, trace_id: str):
        """Set trace ID for current context."""
        trace_id_var.set(trace_id)
    
    def get_trace_id(self) -> Optional[str]:
        """Get trace ID from current context."""
        return trace_id_var.get()
    
    def extract_from_headers(self, headers: Dict[str, str]) -> tuple[Optional[str], Optional[str]]:
        """Extract correlation ID and trace ID from HTTP headers."""
        correlation_id = (
            headers.get(self.correlation_header) or 
            headers.get(self.request_id_header)
        )
        trace_id = headers.get(self.trace_header)
        return correlation_id, trace_id
    
    def inject_into_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Inject correlation ID and trace ID into HTTP headers."""
        correlation_id = self.get_or_create_correlation_id()
        headers[self.correlation_header] = correlation_id
        
        trace_id = self.get_trace_id()
        if trace_id:
            headers[self.trace_header] = trace_id
        
        return headers
    
    def create_child_context(self) -> Dict[str, Any]:
        """Create context for child operations."""
        return {
            'correlation_id': self.get_or_create_correlation_id(),
            'trace_id': self.get_trace_id(),
            'parent_operation': 'framework_execution'
        }


# Global correlation manager
correlation_manager = CorrelationManager()