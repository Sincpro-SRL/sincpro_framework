"""Tests for environment variable handling in configuration system.

This module verifies the following configuration behaviors:
1. Environment variables are correctly loaded from config files
2. Default values are properly used when environment variables are missing
3. Validation errors are raised for required fields with no default values
"""

import os
import warnings
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


def test_default_values_used_when_environment_variables_missing():
    """Verify that default values are used when referenced environment variables don't exist.

    This test ensures that when a configuration references a non-existent environment
    variable, the system falls back to the default value defined in the model
    and issues an appropriate warning.
    """
    # Arrange & Act: Capture warnings while loading config with missing env var
    with warnings.catch_warnings(record=True) as captured_warnings:
        config = build_config_obj(_ConfigWithDefaults, CONFIG_WITH_MISSING_ENV_VARS)

        # Assert: Warning was issued about the missing environment variable
        assert len(captured_warnings) == 1
        warning_message = str(captured_warnings[0].message)
        assert "NON_EXISTENT_VAR" in warning_message
        assert "default value" in warning_message

    # Assert: Default values were used instead
    assert config.string_value == "default_string"
    assert config.int_value == 42  # Unchanged from default


def test_validation_error_for_required_fields_with_missing_env_vars():
    """Verify that validation errors occur for required fields with missing env vars.

    This test ensures that when a required field (one without a default value)
    references a non-existent environment variable, the system issues a warning
    and then Pydantic raises a validation error as expected.
    """
    # Arrange & Act: Attempt to load config with required field but missing env var
    with warnings.catch_warnings(record=True) as captured_warnings:
        # Assert: ValidationError is raised
        with pytest.raises(ValidationError):
            build_config_obj(_ConfigWithRequiredField, CONFIG_WITH_REQUIRED_FIELD)

        # Assert: Warning was issued before the error
        assert len(captured_warnings) == 1
        assert "NON_EXISTENT_VAR" in str(captured_warnings[0].message)
