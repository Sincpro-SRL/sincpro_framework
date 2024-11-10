"""Logger module for framework."""

import logging
from typing import Any, Literal

import structlog


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

    logging.basicConfig(level=log_level)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    )

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
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )


configure_global_logging()


def create_logger(name: str) -> Any:
    """Create a logger with the specified name and level."""
    new_instance_logger = structlog.get_logger(name).bind(app_name=name)
    return new_instance_logger


logger = create_logger("sincpro_framework")
