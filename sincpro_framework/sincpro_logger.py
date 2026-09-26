"""Logger module for framework."""

from sincpro_log import configure_global_logging, create_logger, ignore_callsite_of

from .sincpro_conf import settings

# The bus logs on behalf of whoever executed the DTO: its lines name that caller, not bus.py.
ignore_callsite_of("sincpro_framework")

configure_global_logging(
    settings.sincpro_framework_log_level,
    backend=settings.sincpro_framework_log_backend,
    file_path=settings.sincpro_framework_log_file_path,
)


def is_logger_in_debug() -> bool:
    """Check if the logger is in debug mode."""
    return settings.sincpro_framework_log_level == "DEBUG"


logger = create_logger("sincpro_framework")

__all__ = [
    "create_logger",
    "configure_global_logging",
    "settings",
    "logger",
    "is_logger_in_debug",
]
