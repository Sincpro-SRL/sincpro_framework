"""The environment by path — `PAYMENTS__QR__TIMEOUT=5` sets `qr.timeout` — behind a declared prefix.

Context: a variable of the prefix becomes, in the document, the `$ENV:` sentinel of its path — as
if the file said `timeout: $ENV:PAYMENTS__QR__TIMEOUT` there. So it wins over the file, cascades
into the sections below like any value, and an unusable one falls back to the default with the
same log line a sentinel always had. Paths are below the project's own section — the first key of
`sub_key` — so a context resolved alone reads the same variables as the global.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.settings.domain.config import logger
from sincpro_framework.settings.domain.resolution import ENV_SENTINEL

SEPARATOR = "__"


def _placed(
    document: Mapping[str, Any], keys: list[str], value: str
) -> dict[str, Any] | None:
    """`document` with `value` at `keys` — each section on the way copied, never changed in
    place; `None` when the way crosses a value that is not a section."""
    copied = dict(document)
    if len(keys) == 1:
        copied[keys[0]] = value
        return copied
    inner = copied.get(keys[0], {})
    if not isinstance(inner, Mapping):
        return None
    placed = _placed(inner, keys[1:], value)
    if placed is None:
        return None
    copied[keys[0]] = placed
    return copied


def with_environment_by_path(
    document: Mapping[str, Any],
    prefix: str,
    anchor: str | None,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    """The document with every `<PREFIX>__<PATH>` variable of `environ` placed at its path.

    PAYMENTS__QR__TIMEOUT=5, anchor "sincpro_payments_sdk"
    →  document["sincpro_payments_sdk"]["qr"]["timeout"] == "$ENV:PAYMENTS__QR__TIMEOUT"
    """
    head = f"{prefix.upper()}{SEPARATOR}"
    placed = dict(document)
    for name in sorted(environ):
        if not name.upper().startswith(head):
            continue
        path = [part.lower() for part in name[len(head) :].split(SEPARATOR) if part]
        if not path:
            continue
        keys = [anchor, *path] if anchor else path
        moved = _placed(placed, keys, f"{ENV_SENTINEL}{name}")
        if moved is None:
            logger.warning(
                f"[{name}] names {'.'.join(keys)}, which crosses a value that is not a section "
                "— ignored"
            )
            continue
        placed = moved
    return placed
