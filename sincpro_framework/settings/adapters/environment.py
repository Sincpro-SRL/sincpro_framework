"""The environment by path — `PAYMENTS__QR__TIMEOUT=5` sets `qr.timeout` — behind a declared prefix.

Context: a variable of the prefix becomes, in the document, the `$ENV:` sentinel of its path — as
if the file said `timeout: $ENV:PAYMENTS__QR__TIMEOUT` there. So it wins over the file, cascades
into the sections below like any value, and an unusable one falls back to the default with the
same log line a sentinel always had. Paths are below the project's own section — the first key of
`sub_key` — so a context resolved alone reads the same variables as the global.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.settings.domain.config import ENV_SENTINEL, logger

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

        with_environment_by_path({"sincpro_payments_sdk": {"qr": {"timeout": 30}}},
                                 "payments", "sincpro_payments_sdk",
                                 {"PAYMENTS__QR__TIMEOUT": "5", "HOME": "/root"})
            ->  {"sincpro_payments_sdk": {"qr": {"timeout": "$ENV:PAYMENTS__QR__TIMEOUT"}}}
        # PAYMENTS__QR__TIMEOUT__UNIT=s, with qr.timeout a value  ->  warned, ignored

    1. The head every variable of the prefix starts with: `<PREFIX>__`, in upper case.
    2. Each variable of `environ`, by name in order:
       2.1 one without the head, or naming no path after it (`PAYMENTS__`), is passed by;
       2.2 its path: the parts between `__`, lower-cased, empty parts dropped — below
           `anchor` when one is given;
       2.3 the `$ENV:<NAME>` sentinel placed there, every section on the way copied;
       2.4 a path crossing a value that is not a section is logged and ignored.
    Final: the document with every sentinel placed — `document` itself left untouched.
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
