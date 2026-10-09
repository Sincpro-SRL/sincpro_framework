"""What a class annotates itself, read the same way on every Python the framework runs on.

    own_annotations(InvoicePosted)     →  {"name": <class 'str'>, "total": …}
    is_class_var(ClassVar[str])        →  True

Context: Python 3.14 evaluates annotations lazily (PEP 649, PEP 749). While a class is being
created — inside `__init_subclass__` — its `__annotations__` is not in the class namespace yet,
so `cls.__dict__.get("__annotations__")` answers nothing, and evaluating them may name a class
defined further down the module. The names are read as forward references instead: the question
«what does this class annotate» never fails, and never waits for the module to finish.
"""

import sys
from typing import Any, ClassVar, get_origin

if sys.version_info >= (3, 14):
    from annotationlib import Format, get_annotations

    def own_annotations(cls: type) -> dict[str, Any]:
        """The annotations `cls` declares itself, not its bases', as forward references."""
        return dict(get_annotations(cls, format=Format.FORWARDREF))

else:

    def own_annotations(cls: type) -> dict[str, Any]:
        """The annotations `cls` declares itself, not its bases'."""
        return dict(cls.__dict__.get("__annotations__", {}))


def is_class_var(annotation: Any) -> bool:
    """Whether an annotation declares a class attribute rather than a field.

    in      ClassVar[str], "ClassVar[str]"     →  out  True
    in      int, "int"                         →  out  False
    """
    if isinstance(annotation, str):
        return annotation.startswith(("ClassVar", "typing.ClassVar"))
    return annotation is ClassVar or get_origin(annotation) is ClassVar
