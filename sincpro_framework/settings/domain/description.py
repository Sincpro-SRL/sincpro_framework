"""`SettingDescription`: one value of a settings object, and where it came from."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingDescription:
    """One leaf of a settings object: its dotted path, its annotation as written, its value
    (masked when it is a secret) and where that value came from.

        SettingDescription("qr.timeout", "float", 10.0, "default")
    """

    path: str
    type: str
    value: Any
    source: str
    """`default`, `file <path> at <section>`, `inherited from <section>`, `env <NAME>`,
    `default (<NAME> not set)`, `default (<NAME> cannot be used)` — or
    `assigned` for a path the build did not record (an object built by hand). A value assigned
    after building still names the source it was built with; `value` is always the current one."""
