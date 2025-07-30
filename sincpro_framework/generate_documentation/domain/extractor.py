"""
Metadata Extractor Module

This module contains pure functions to extract metadata from different types of Python objects.
All functions are independent and have no side effects.
"""

import inspect
from typing import Any, Dict, List

from sincpro_framework.generate_documentation.domain.models import (
    ClassMetadata,
    FunctionMetadata,
    InstanceMetadata,
    PydanticModelMetadata,
)


def _get_real_module_info(obj) -> tuple[str, str]:
    """
    Get real module information from object, trying to derive meaningful namespace
    from source file when __module__ is __main__
    """
    module_name = getattr(obj, "__module__", "Unknown")
    source_file = "Unknown"

    try:
        source_file = inspect.getfile(obj)

        # If module is __main__, try to derive a better name from the file path
        if module_name == "__main__" and source_file != "Unknown":
            from pathlib import Path

            # Get the file path
            file_path = Path(source_file).resolve()  # Get absolute path

            # Special handling for Jupyter notebooks
            if file_path.suffix == ".py" and "ipynb" in source_file:
                # This is likely a temp file from Jupyter, try to get notebook info
                try:
                    # Try to get the original notebook name from the temp file pattern
                    if "ipykernel_" in str(file_path):
                        # It's a Jupyter kernel temp file, use a generic name
                        module_name = "jupyter.notebook"
                    else:
                        module_name = "jupyter.script"
                except:
                    module_name = "jupyter.session"
            else:
                # Try to find a meaningful project structure
                parts = file_path.parts

                # Look for common project indicators
                project_indicators = [
                    "sincpro_framework",
                    "src",
                    "app",
                    "lib",
                    "project",
                    "examples",
                    "tests",
                    "docs",
                ]

                meaningful_start = None
                for i, part in enumerate(parts):
                    if part in project_indicators:
                        meaningful_start = i
                        break

                if meaningful_start is not None:
                    # Extract from the project indicator onwards
                    meaningful_parts = parts[meaningful_start:]
                    module_parts = []

                    for part in meaningful_parts:
                        if part.endswith(".py"):
                            module_parts.append(part[:-3])  # Remove .py
                        else:
                            module_parts.append(part)

                    module_name = ".".join(module_parts)
                else:
                    # Fallback: use the file structure from a reasonable depth
                    # Take last 2-3 meaningful parts
                    if len(parts) >= 2:
                        # Take last 2 parts for context
                        meaningful_parts = parts[-2:]
                        module_parts = []

                        for part in meaningful_parts:
                            if part.endswith(".py"):
                                module_parts.append(part[:-3])  # Remove .py
                            else:
                                module_parts.append(part)

                        module_name = ".".join(module_parts)
                    else:
                        # Last resort: just the filename
                        if file_path.suffix == ".py":
                            module_name = file_path.stem
                        else:
                            module_name = str(file_path.name)

    except Exception:
        # If all fails, try to get some info from the object itself
        try:
            if hasattr(obj, "__qualname__"):
                # Use qualified name as fallback
                module_name = f"dynamic.{obj.__qualname__}"
            elif hasattr(obj, "__name__"):
                module_name = f"dynamic.{obj.__name__}"
            else:
                module_name = "dynamic.object"
        except:
            module_name = "unknown.module"

    return module_name, source_file


def extract_function_metadata(func) -> FunctionMetadata:
    """Extract metadata from a function."""
    sig = inspect.signature(func)

    parameters = {}
    for name, param in sig.parameters.items():
        parameters[name] = {
            "type": (
                str(param.annotation)
                if param.annotation != inspect.Parameter.empty
                else "Any"
            ),
            "default": (
                str(param.default) if param.default != inspect.Parameter.empty else None
            ),
            "required": param.default == inspect.Parameter.empty,
        }

    # Get source info directly
    source_file = "Unknown"
    source_line = 0
    try:
        source_file = inspect.getfile(func)
    except:
        pass
    try:
        source_line = inspect.getsourcelines(func)[1]
    except:
        pass

    module_name, source_file = _get_real_module_info(func)

    return FunctionMetadata(
        name=func.__name__,
        module=module_name,
        docstring=inspect.getdoc(func),
        signature=str(sig),
        parameters=parameters,
        return_type=(
            str(sig.return_annotation)
            if sig.return_annotation != inspect.Signature.empty
            else "Any"
        ),
        is_async=inspect.iscoroutinefunction(func),
        is_generator=inspect.isgeneratorfunction(func),
        source_file=source_file,
        source_line=source_line,
    )


def extract_class_metadata(cls) -> ClassMetadata:
    """Extract metadata from a class."""
    methods = {}
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if not name.startswith("_"):
            methods[name] = extract_function_metadata(method)

    attributes = {}
    for name in dir(cls):
        if not name.startswith("_") and not callable(getattr(cls, name, None)):
            try:
                attr = getattr(cls, name)
                attributes[name] = type(attr).__name__
            except:
                attributes[name] = "Unknown"

    # Get source info directly
    source_file = "Unknown"
    source_line = 0
    try:
        source_file = inspect.getfile(cls)
    except:
        pass
    try:
        source_line = inspect.getsourcelines(cls)[1]
    except:
        pass

    module_name, source_file = _get_real_module_info(cls)

    return ClassMetadata(
        name=cls.__name__,
        module=module_name,
        docstring=inspect.getdoc(cls),
        bases=[base.__name__ for base in cls.__bases__],
        mro=[mro_cls.__name__ for mro_cls in cls.__mro__],
        methods=methods,
        attributes=attributes,
        source_file=source_file,
        source_line=source_line,
    )


def extract_pydantic_model_metadata(model_cls) -> PydanticModelMetadata:
    """Extract metadata from a Pydantic model."""
    fields = {}
    validators = []

    # Extract model fields
    if hasattr(model_cls, "model_fields"):  # Pydantic V2
        for field_name, field_info in model_cls.model_fields.items():
            fields[field_name] = {
                "type": str(field_info.annotation),
                "required": field_info.is_required(),
                "default": field_info.default if field_info.default is not None else None,
                "description": field_info.description,
            }
    elif hasattr(model_cls, "__fields__"):  # Pydantic V1
        for field_name, field_info in model_cls.__fields__.items():
            fields[field_name] = {
                "type": str(field_info.type_),
                "required": field_info.required,
                "default": field_info.default,
                "description": field_info.field_info.description,
            }

    # Extract validators
    if hasattr(model_cls, "__validators__"):
        validators = list(model_cls.__validators__.keys())

    # Model schema
    model_schema = None
    try:
        if hasattr(model_cls, "model_json_schema"):  # Pydantic V2
            model_schema = model_cls.model_json_schema()
        elif hasattr(model_cls, "schema"):  # Pydantic V1
            model_schema = model_cls.schema()
    except:
        pass

    # Get source info directly
    source_file = "Unknown"
    source_line = 0
    try:
        source_file = inspect.getfile(model_cls)
    except:
        pass
    try:
        source_line = inspect.getsourcelines(model_cls)[1]
    except:
        pass

    module_name, source_file = _get_real_module_info(model_cls)

    return PydanticModelMetadata(
        name=model_cls.__name__,
        module=module_name,
        docstring=inspect.getdoc(model_cls),
        bases=[base.__name__ for base in model_cls.__bases__],
        fields=fields,
        model_schema=model_schema,
        config=getattr(model_cls, "model_config", None),
        validators=validators,
        source_file=source_file,
        source_line=source_line,
    )


def extract_instance_metadata(obj) -> InstanceMetadata:
    """Extract metadata from an object instance."""
    cls = obj.__class__

    return InstanceMetadata(
        class_name=cls.__name__,
        module=cls.__module__,
        object_id=str(id(obj)),
        object_repr=repr(obj),
        class_docstring=inspect.getdoc(cls),
        is_pydantic=is_pydantic_model_instance(obj),
        public_attributes=_get_public_attributes(obj),
        public_methods=_get_public_methods(obj),
        inheritance=[base.__name__ for base in cls.__mro__[1:] if base.__name__ != "object"],
    )


def is_pydantic_model_class(cls) -> bool:
    """Check if a class is a Pydantic model."""
    return (
        hasattr(cls, "model_fields")
        or hasattr(cls, "__fields__")
        or any("pydantic" in str(base).lower() for base in cls.__mro__)
    )


def is_pydantic_model_instance(obj) -> bool:
    """Check if an object is an instance of a Pydantic model."""
    return hasattr(obj, "model_fields") or hasattr(obj, "__fields__")


def classify_and_extract_objects(objects: List[Any]) -> Dict[str, List]:
    """
    Classify a list of mixed objects and extract their metadata by type.
    This is the main entry point - it automatically determines object types.
    """
    result = {"functions": [], "classes": [], "pydantic_models": [], "instances": []}

    for obj in objects:
        if inspect.isfunction(obj):
            result["functions"].append(extract_function_metadata(obj))
        elif inspect.isclass(obj):
            if is_pydantic_model_class(obj):
                result["pydantic_models"].append(extract_pydantic_model_metadata(obj))
            else:
                result["classes"].append(extract_class_metadata(obj))
        else:
            # It's an instance
            result["instances"].append(extract_instance_metadata(obj))

    return result


# Private utility functions
def _get_public_attributes(obj) -> Dict[str, Dict[str, str]]:
    """Get public attributes of an object."""
    attributes = {}
    for attr_name in dir(obj):
        if not attr_name.startswith("_") and not callable(getattr(obj, attr_name, None)):
            try:
                attr_value = getattr(obj, attr_name)
                attributes[attr_name] = {
                    "value": (
                        str(attr_value)[:100] + "..."
                        if len(str(attr_value)) > 100
                        else str(attr_value)
                    ),
                    "type": type(attr_value).__name__,
                }
            except:
                attributes[attr_name] = {"value": "<Error accessing>", "type": "Unknown"}
    return attributes


def _get_public_methods(obj) -> Dict[str, Dict[str, str]]:
    """Get public methods of an object."""
    methods = {}
    for method_name in dir(obj):
        if not method_name.startswith("_") and callable(getattr(obj, method_name, None)):
            try:
                method = getattr(obj, method_name)
                if inspect.ismethod(method) or inspect.isfunction(method):
                    methods[method_name] = {
                        "signature": str(inspect.signature(method)),
                        "docstring": inspect.getdoc(method) or "No documentation",
                    }
            except:
                methods[method_name] = {
                    "signature": "<Error>",
                    "docstring": "Error accessing",
                }
    return methods
