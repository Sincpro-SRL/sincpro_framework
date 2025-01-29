"""Logger module for framework."""

import logging
from functools import partial
from typing import Any, Callable, Literal

import structlog

from .sincpro_conf import settings


class LoggerProxy:
    """Proxy logger"""

    __slots__ = ("_name", "_binds")

    def __init__(self, app_name: str):
        self._name = app_name
        self._binds = {"app_name": app_name}

    def bind(self, **kwargs: Any) -> "LoggerProxy":
        self._binds.update(kwargs)
        return self

    def unbind(self, *keys: str) -> "LoggerProxy":
        for k in keys:
            self._binds.pop(k, None)
        return self

    @property
    def debug(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).debug)

    @property
    def info(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).info)

    @property
    def warning(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).warning)

    @property
    def error(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).error)

    @property
    def exception(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).exception)

    @property
    def critical(self) -> Callable[..., None]:
        return partial(structlog.get_logger(self._name).bind(**self._binds).critical)


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


def create_logger(name: str) -> LoggerProxy:
    """Create a logger with the specified name and level."""
    new_instance_logger = LoggerProxy(name)
    return new_instance_logger


def is_logger_in_debug() -> bool:
    """Check if the logger is in debug mode."""
    return settings.sincpro_framework_log_level == "DEBUG"


logger = create_logger("sincpro_framework")
