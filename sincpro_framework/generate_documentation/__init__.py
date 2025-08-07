"""
Sincpro Framework Auto-Documentation System

This module provides comprehensive documentation generation capabilities for Sincpro Framework applications.
It can generate both traditional MkDocs-ready markdown documentation and AI-optimized chunked JSON schemas.
"""

from sincpro_framework.generate_documentation.service import (
    build_documentation,
    generate_chunked_json_schema,
)

__all__ = ["build_documentation", "generate_chunked_json_schema"]
