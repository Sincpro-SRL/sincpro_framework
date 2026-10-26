"""What a registered class is known by — an event its `name`, any other DTO `context.Class` —
and the cases a name is written in on each wire."""

from sincpro_framework.common.naming.case import kebab, pascal, snake, upper_snake
from sincpro_framework.common.naming.registered_name import local_name, registered_name

__all__ = [
    "kebab",
    "local_name",
    "pascal",
    "registered_name",
    "snake",
    "upper_snake",
]
