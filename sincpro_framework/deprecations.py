"""What still works, and says it will not in the next major version.

Context: a class that was a dataclass and became a `DataTransferObject` stops taking its fields
positionally — pydantic builds by keyword. `PositionalFields` keeps the old form alive for the
minor versions in between, in field order, with a `DeprecationWarning` naming the keywords to
write instead.
"""

import warnings
from typing import TYPE_CHECKING, Any


class PositionalFields:
    """Mixed in before `DataTransferObject`: `Run(name, scheduled_for, key, ...)` still builds a
    `Run`, and warns. Hidden from the type checker, which keeps checking keyword construction.
    """

    if not TYPE_CHECKING:

        def __init__(self, *args: Any, **values: Any) -> None:
            if args:
                names = list(type(self).model_fields)
                if len(args) > len(names):
                    raise TypeError(
                        f"{type(self).__name__} takes {len(names)} fields, {len(args)} were given"
                    )
                named = ", ".join(f"{name}=" for name in names[: len(args)])
                warnings.warn(
                    f"{type(self).__name__}(...) with positional fields is deprecated and goes "
                    f"in the next major version: write {type(self).__name__}({named}...)",
                    DeprecationWarning,
                    stacklevel=2,
                )
                values = {**dict(zip(names, args)), **values}
            super().__init__(**values)
