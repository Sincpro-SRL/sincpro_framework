"""Logger module for framework."""

from sincpro_log import configure_global_logging, create_logger

from .sincpro_conf import settings

configure_global_logging(settings.sincpro_framework_log_level)


def is_logger_in_debug() -> bool:
    """Check if the logger is in debug mode."""
    return settings.sincpro_framework_log_level == "DEBUG"


logger = create_logger("sincpro_framework")
