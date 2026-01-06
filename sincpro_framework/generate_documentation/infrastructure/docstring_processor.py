"""
Shared utilities for documentation generation.

This module contains utility functions that are used across different
documentation generators to ensure consistent processing.
"""


def process_docstring_for_documentation(docstring: str, component_name: str) -> str:
    """
    Process docstring to extract useful information and remove redundant framework explanations.

    This function is shared between Markdown and JSON generators to ensure consistent
    processing of docstrings across all documentation formats.

    Args:
        docstring: The raw docstring to process
        component_name: Name of the component this docstring belongs to

    Returns:
        Processed docstring with framework explanations removed/truncated
    """
    if not docstring or not docstring.strip():
        return ""

    # Define patterns that indicate generic framework explanations
    framework_patterns = [
        "Second layer of the framework",
        "ApplicationServices coordinate multiple Features",
        "Features are atomic operations",
        "They have access to all injected dependencies",
        "ApplicationServices are ideal for:",
        "For better IDE support with typed dependencies",
        "For backward compatibility",
        "Example:",
        "@framework.app_service",
        "@framework.feature",
        "# Type your injected dependencies",
        "def execute(self, dto:",
        "class MyApplicationService",
        "class MyFeature",
        "feature_bus for executing",
        "Non-atomic operations requiring",
        "Coordinating between different Features",
        "Complex business workflows with",
        "Aggregating data from multiple sources",
        "external_service:",
        "correlation_id =",
        "user_id =",
        "step1_result =",
        "step2_result =",
        "final_result =",
        "return MyResponseDTO",
        "# Access context",
        "# Execute Features",
        "# Use injected dependencies",
        "# This still works",
    ]

    lines = docstring.strip().split("\n")
    processed_lines = []
    found_example_or_code = False

    for line in lines:
        line_stripped = line.strip()

        # Skip empty lines at the beginning
        if not line_stripped and not processed_lines:
            continue

        # Stop processing completely when we hit example/code sections
        if (
            "Example:" in line
            or "@framework." in line
            or "class My" in line
            or "def execute(self, dto:" in line
            or line.strip().startswith("@")
            or "external_service:" in line
            or "correlation_id =" in line
            or "return MyResponseDTO" in line
        ):
            found_example_or_code = True
            break

        # Check if this line contains generic framework explanations
        is_framework_pattern = any(pattern in line for pattern in framework_patterns)

        # Skip lines that contain framework patterns
        if is_framework_pattern:
            continue

        # Skip bullet points that are generic framework features
        if line_stripped.startswith("- ") and any(
            pattern.lower() in line.lower()
            for pattern in [
                "non-atomic",
                "coordinating",
                "complex business",
                "aggregating data",
                "multiple steps",
            ]
        ):
            continue

        # Keep the line if it doesn't match framework patterns
        if line_stripped:
            processed_lines.append(line.strip())
        elif processed_lines and processed_lines[-1]:  # Keep empty lines between paragraphs
            processed_lines.append("")

    # Remove trailing empty lines
    while processed_lines and not processed_lines[-1]:
        processed_lines.pop()

    # Join the processed lines
    result = "\n".join(processed_lines).strip()

    # If we ended up with nothing useful, try to extract just the first meaningful sentence
    if not result or len(result) < 10:
        first_lines = docstring.strip().split("\n")[:3]  # Check first 3 lines
        for line in first_lines:
            line = line.strip()
            if line and not any(pattern in line for pattern in framework_patterns):
                # Don't include lines that are clearly part of framework explanation
                if not (
                    "ApplicationService" in line
                    and ("coordinate" in line or "Features" in line)
                ):
                    # Extract just the first sentence
                    sentences = line.split(".")
                    if sentences and sentences[0].strip():
                        result = sentences[0].strip()
                        if result and not result.endswith("."):
                            result += "."
                        break

    # Final fallback - if still empty, return a generic description
    if not result:
        if "ApplicationService" in component_name or "Service" in component_name:
            result = "Application service component"
        elif "Feature" in component_name:
            result = "Feature component"
        else:
            result = "Framework component"

    return result
