"""`describe_settings`: each value of a settings object, and where it came from.

    for one in describe_settings(settings):
        print(one.path, one.type, one.value, one.source)
    # qr.timeout            float  10.0         default
    # qr.environment        str    'TEST'       inherited from sincpro_payments_sdk
    # cybersource.api_secret Secret '**********' env CYBERSOURCE_API_SECRET

For a person debugging a deployment, or an agent writing one. A secret's value is never shown.
"""

from dataclasses import dataclass
from typing import Any, get_args

from pydantic import Secret

from sincpro_framework.settings.domain.config import SincproConfig
from sincpro_framework.settings.domain.resolution import nested_shape

MASKED = "**********"


@dataclass(frozen=True)
class SettingDescription:
    path: str
    type: str
    value: Any
    source: str
    """`default`, `file <path> at <section>`, `inherited from <section>`, `env <NAME>`,
    `default (<NAME> not set)`, `default (<NAME> cannot be used)` — or
    `assigned` for a path the build did not record (an object built by hand). A value assigned
    after building keeps the source it was built with; `value` is always the current one."""


def _type_name(annotation: Any) -> str:
    """`float`, `Secret[str] | None`, `Literal['INFO', 'DEBUG']` — the annotation as written."""
    if isinstance(annotation, type) and not get_args(annotation):
        return annotation.__name__
    return repr(annotation).replace("typing.", "").replace("pydantic.types.", "")


def _walk(
    settings: SincproConfig, sources: dict[str, str], prefix: str
) -> list[SettingDescription]:
    described: list[SettingDescription] = []
    for name, field in type(settings).model_fields.items():
        at = f"{prefix}{name}"
        value = getattr(settings, name)
        if nested_shape(field.annotation) is not None and isinstance(value, SincproConfig):
            described += _walk(value, sources, f"{at}.")
            continue
        shown = MASKED if isinstance(value, Secret) else value
        described.append(
            SettingDescription(
                at, _type_name(field.annotation), shown, sources.get(at, "assigned")
            )
        )
    return described


def describe_settings(settings: SincproConfig) -> list[SettingDescription]:
    """Every value of `settings`, nested sections included, by path, in declaration order."""
    return _walk(settings, settings._sources, "")
