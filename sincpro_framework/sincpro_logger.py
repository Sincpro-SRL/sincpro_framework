"""Logger module for framework."""

import logging
from typing import Any, Literal

import structlog

from .sincpro_conf import settings


def configure_global_logging(level: Literal["INFO", "DEBUG"] = "DEBUG") -> None:
    """Global configuration logging shared by all loggers."""
    log_level = None
    render_type = None

    match level:
        case "DEBUG":
            log_level = logging.DEBUG
            render_type = structlog.dev.ConsoleRenderer()
        case "INFO":
            log_level = logging.INFO
            render_type = structlog.processors.JSONRenderer()
        case _:
            raise ValueError(f"Invalid log level: {level}")

    processors = [
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        render_type,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )


configure_global_logging(settings.sincpro_framework_log_level)


def create_logger(name: str) -> Any:
    """Create a logger with the specified name and level."""
    new_instance_logger = structlog.get_logger(name).bind(app_name=name)
    return new_instance_logger


logger = create_logger("sincpro_framework")
