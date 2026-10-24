"""How a shape is resolved against the document: its section, its nested shapes, the cascade.

    section, above = locate(document, "sincpro_payments_sdk.qr", file)
    values = values_of(QRSettings, section, "sincpro_payments_sdk.qr", file, above, sources)
    QRSettings(**values)                               # validated whole, once

Context: the document is the configuration as written — a dict read from the project's file.
Resolving a shape builds the values it is validated from, and where each came from; pydantic then
validates the whole tree in one call, so every problem is one error naming each path.
"""

import inspect
import os
from collections.abc import Mapping, Sequence
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic.fields import FieldInfo

from sincpro_framework.settings.domain.config import (
    SENTINEL,
    SincproConfig,
    is_secret,
    logger,
    usable_env_value,
)

ENV_SENTINEL = "$ENV:"

type Above = Sequence[tuple[str, Mapping[str, Any]]]
"""The sections above one, nearest first, each with its path — where the cascade looks."""


def locate(
    document: Mapping[str, Any], sub_key: str | None, file: str
) -> tuple[dict[str, Any], list[tuple[str, Mapping[str, Any]]]]:
    """The section `sub_key` names — a dotted path — and the sections above it, nearest first.

    Context: the first key of the path is the project's own section of the file; nothing beside
    it or above it is the project's, so the cascade never reaches past it — a flat class read at
    `"my_project"`, as every project reads it today, takes nothing from the rest of the file.

        document = {"payments": {"environment": "test", "qr": {"timeout": 5}}, "other": {}}
        locate(document, "payments.qr", "conf.yaml")
            ->  ({"timeout": 5}, [("payments", {"environment": "test", "qr": {…}})])
        locate(document, "payments", "conf.yaml")    ->  ({"environment": "test", …}, [])
        locate(document, None, "conf.yaml")          ->  (the whole document, [])
        locate(document, "payments.qr.timeout", "conf.yaml")
            ->  raises ValueError("Config section payments.qr.timeout in conf.yaml is not
                a section")

    1. No `sub_key`: the whole document, nothing above it.
    2. Down the dotted path, key by key:
       2.1 past the first key, the section just left is put first among those above — the
           document itself never is;
       2.2 a key missing on the way is `ValueError`, naming the path and the file.
    3. What the path reaches must be a section, else `ValueError`.
    Final: a copy of the section, and the sections above it, nearest first.
    """
    if not sub_key:
        return dict(document), []
    keys = sub_key.split(".")
    above: list[tuple[str, Mapping[str, Any]]] = []
    section: Any = document
    for depth, key in enumerate(keys):
        if depth:
            above.insert(0, (".".join(keys[:depth]), section))
        section = section.get(key) if isinstance(section, Mapping) else None
        if section is None:
            raise ValueError(f"Config section {sub_key} not found in {file}")
    if not isinstance(section, Mapping):
        raise ValueError(f"Config section {sub_key} in {file} is not a section")
    return dict(section), above


def nested_shape(annotation: Any) -> type[SincproConfig] | None:
    """The shape a field holds — `QRSettings`, or `QRSettings | None` — or `None` for a value.

        nested_shape(QRSettings)                  ->  QRSettings
        nested_shape(QRSettings | None)           ->  QRSettings
        nested_shape(QRSettings | CardSettings)   ->  None    # which one is not said
        nested_shape(list[QRSettings])            ->  None    # a list is a value
        nested_shape(int)                         ->  None

    A union holding exactly one shape is that shape; two or more say nothing.
    """
    if isinstance(annotation, type) and issubclass(annotation, SincproConfig):
        return annotation
    if get_origin(annotation) in (Union, UnionType):
        shapes = [one for one in get_args(annotation) if nested_shape(one) is not None]
        return nested_shape(shapes[0]) if len(shapes) == 1 else None
    return None


def _source(value: Any, where: str, field: FieldInfo) -> str:
    """Where a written value comes from — for `$ENV:NAME`, what the variable really gave: the
    variable, or the field's default when it is unset or holds what the field cannot accept.
    """
    found = SENTINEL.match(value) if isinstance(value, str) else None
    if found is None:
        return where
    if found.group(1) != "ENV":
        return f"{found.group(1).lower()} {found.group(2)}"
    name = value[len(ENV_SENTINEL) :]
    held = os.getenv(name)
    if held is None:
        return f"default ({name} not set)"
    if not usable_env_value(held, field):
        return f"default ({name} cannot be used)"
    return f"env {name}"


def is_shared(shape: type[SincproConfig], name: str) -> bool:
    """Whether `shape` has the field `name` from a shape it inherits — the shared settings — and
    not by declaring it itself. Only a shared field cascades.

        class QRSettings(SharedSettings): ...   # environment: shared, taken from above
        class PostgresConf(SincproConfig):
            log_level: str = "INFO"             # its own: never taken from above
    """
    owner = next(one for one in shape.__mro__ if name in inspect.get_annotations(one))
    return owner is not shape


def _defaults_of(shape: type[SincproConfig], prefix: str, sources: dict[str, str]) -> None:
    """Record every leaf of `shape` as `default` in `sources`, nested shapes walked into —
    for a section left out of the file, whose fields all keep their defaults.

        _defaults_of(PostgresConf, "postgresql.", sources)
            ->  sources {"postgresql.host": "default", "postgresql.log_level": "default"}
    """
    for name, field in shape.model_fields.items():
        inner = nested_shape(field.annotation)
        if inner is not None:
            _defaults_of(inner, f"{prefix}{name}.", sources)
        else:
            sources[f"{prefix}{name}"] = "default"


def values_of(
    shape: type[SincproConfig],
    section: Mapping[str, Any],
    where: str,
    file: str,
    above: Above,
    sources: dict[str, str],
    prefix: str = "",
) -> dict[str, Any]:
    """The values `shape` is built from, out of its `section` at path `where`.

    1. Every key of its section, as written — a flat class gets exactly what it always got.
    2. Each field that is itself a shape: resolved the same way, at the section of its name, with
       this section the nearest one above it (recursion). One with a default whose section is
       absent keeps its default — `postgresql: PostgresConf = PostgresConf()`, as always; one
       written as a non-section (`null`) is passed as written.
    3. Each shared field the section does not set — one the shape has from a shape it inherits,
       not one it declares (`is_shared`): the value of the nearest section above that sets it —
       the cascade. A field a shape declares itself is never taken from above, so a nested class
       written before the cascade existed builds exactly as it did.
    4. Final: the values, and in `sources` where each field's came from, by path.

        section {"linkser_endpoint": "$ENV:LINKSER"}, above [("root", {"environment": "TEST"})]
        →  {"linkser_endpoint": "$ENV:LINKSER", "environment": "TEST"}
           sources {"linkser_endpoint": "env LINKSER", "environment": "inherited from root"}

    A `$ENV:` value's source is the variable when it is set and usable, else
    `default (<NAME> not set)` or `default (<NAME> cannot be used)` — as the value resolves.
    """
    values = dict(section)
    for name, field in shape.model_fields.items():
        at = f"{prefix}{name}"
        inner = nested_shape(field.annotation)
        if inner is not None:
            child = section.get(name)
            if name not in section and not field.is_required():
                _defaults_of(inner, f"{at}.", sources)
                continue
            if name in section and not isinstance(child, Mapping):
                sources[at] = f"file {file} at {where or '(root)'}"
                continue
            values[name] = values_of(
                inner,
                child if isinstance(child, Mapping) else {},
                f"{where}.{name}" if where else name,
                file,
                [(where, section), *above],
                sources,
                f"{at}.",
            )
            continue
        if name in section:
            sources[at] = _source(section[name], f"file {file} at {where or '(root)'}", field)
            written = section[name]
            if is_secret(field.annotation) and not (
                isinstance(written, str) and SENTINEL.match(written)
            ):
                logger.warning(
                    f"[{at}] is a secret written in {file}: secrets belong in the environment "
                    f"or a vault — write $ENV:<NAME> (or $SSM:, $SECRET:) there instead"
                )
            continue
        shared = is_shared(shape, name)
        inherited = next(((path, one) for path, one in above if shared and name in one), None)
        if inherited is not None:
            path, one = inherited
            values[name] = one[name]
            sources[at] = _source(one[name], f"inherited from {path or '(root)'}", field)
            continue
        sources[at] = "default"
    return values
