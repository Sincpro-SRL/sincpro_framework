"""Logger module for framework."""

from sincpro_logger.logger import create_logger, configure_global_logging, LoggerProxy

from .sincpro_conf import settings


logger = create_logger("sincpro_framework")

configure_global_logging(settings.sincpro_framework_log_level)


def is_logger_in_debug() -> bool:
    """Check if the logger is in debug mode."""
    return settings.sincpro_framework_log_level == "DEBUG"


__all__ = [
    "create_logger",
    "LoggerProxy",
    "configure_global_logging",
]
