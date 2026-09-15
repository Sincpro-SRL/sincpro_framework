from dataclasses import dataclass
from typing import assert_type

from sincpro_framework import DataTransferObject, UseFramework


class Command(DataTransferObject):
    identifier: str


class Response(DataTransferObject):
    total: int


@dataclass
class MappedEntity:
    """A domain mapped to its table imperatively is a plain dataclass, not a DTO."""

    identifier: str


def verify_every_response_type_is_accepted() -> None:
    framework = UseFramework("response-types")
    command = Command(identifier="x")

    assert_type(framework(command, Response), Response)
    assert_type(framework(command, MappedEntity), MappedEntity)
    assert_type(framework(command, list), list)
    assert_type(framework(command, dict), dict)
