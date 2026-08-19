"""Tests for environment variable handling in configuration system.

This module verifies the following configuration behaviors:
1. Environment variables are correctly loaded from config files
2. Default values are properly used when environment variables are missing
3. Validation errors are raised for required fields with no default values
"""

import logging
import os
from typing import Optional

import pytest
from pydantic import ValidationError

from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj

# Test resource file paths
TEST_RESOURCES_PATH = os.path.join(os.path.dirname(__file__), "resources")
CONFIG_WITH_ENV_VARS = os.path.join(TEST_RESOURCES_PATH, "env_vars_test.yml")
CONFIG_WITH_MISSING_ENV_VARS = os.path.join(TEST_RESOURCES_PATH, "missing_env_vars_test.yml")
CONFIG_WITH_REQUIRED_FIELD = os.path.join(TEST_RESOURCES_PATH, "required_field_test.yml")


# Configuration models for testing - prefixed with _ to prevent pytest collection
class _ConfigWithDefaults(SincproConfig):
    """Test configuration model with default values for all fields."""

    string_value: str = "default_string"
    int_value: int = 42
    nested_value: Optional[dict] = None


class _ConfigWithRequiredField(SincproConfig):
    """Test configuration model with a required field (no default value)."""

    required_value: str  # No default = required


def test_environment_variables_are_replaced(monkeypatch):
    """Verify that environment variables in config files are replaced with their values.

    This test ensures that when environment variables are defined in the system,
    the configuration system correctly replaces the $ENV: placeholders with
    the actual values from those environment variables.
    """
    # Arrange: Set environment variables for testing
    monkeypatch.setenv("TEST_STRING", "value_from_environment")
    monkeypatch.setenv("TEST_INT", "100")

    # Act: Build configuration object from file with environment variable references
    config = build_config_obj(_ConfigWithDefaults, CONFIG_WITH_ENV_VARS)

    # Assert: Environment variable values are correctly injected
    assert config.string_value == "value_from_environment"
    assert config.int_value == 100


def test_default_values_used_when_environment_variables_missing(caplog):
    """Verify that default values are used when referenced environment variables don't exist.

    This test ensures that when a configuration references a non-existent environment
    variable, the system falls back to the default value defined in the model
    and logs it at info level.
    """
    with caplog.at_level(logging.INFO, logger="sincpro_framework"):
        config = build_config_obj(_ConfigWithDefaults, CONFIG_WITH_MISSING_ENV_VARS)

    assert config.string_value == "default_string"
    assert config.int_value == 42  # Unchanged from default
    assert any("NON_EXISTENT_VAR" in record.message for record in caplog.records)
    assert all(record.levelno < logging.WARNING for record in caplog.records)


def test_validation_error_for_required_fields_with_missing_env_vars():
    """Verify that validation errors occur for required fields with missing env vars.

    This test ensures that when a required field (one without a default value)
    references a non-existent environment variable, Pydantic raises a validation
    error as expected.
    """
    with pytest.raises(ValidationError):
        build_config_obj(_ConfigWithRequiredField, CONFIG_WITH_REQUIRED_FIELD)
