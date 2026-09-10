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
from pydantic import Field, ValidationError
from typing_extensions import Annotated

from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj

# Test resource file paths
TEST_RESOURCES_PATH = os.path.join(os.path.dirname(__file__), "resources")
CONFIG_WITH_ENV_VARS = os.path.join(TEST_RESOURCES_PATH, "env_vars_test.yml")
CONFIG_WITH_MISSING_ENV_VARS = os.path.join(TEST_RESOURCES_PATH, "missing_env_vars_test.yml")
CONFIG_WITH_REQUIRED_FIELD = os.path.join(TEST_RESOURCES_PATH, "required_field_test.yml")
CONFIG_WITH_RATIO = os.path.join(TEST_RESOURCES_PATH, "ratio_test.yml")


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


class _ConfigWithOptionalField(SincproConfig):
    """The shape of otlp_endpoint and sentry_dsn: absent means the feature is off."""

    string_value: Optional[str] = None


def test_optional_field_without_env_var_stays_none_and_silent(caplog):
    """An unset optional env var disables a feature; it is not a misconfiguration.

    This is the branch otlp_endpoint and sentry_dsn rely on: a deployment with no
    collector and no DSN must build its config without warnings or exceptions.
    """
    with caplog.at_level(logging.INFO, logger="sincpro_framework"):
        config = build_config_obj(_ConfigWithOptionalField, CONFIG_WITH_MISSING_ENV_VARS)

    assert config.string_value is None
    assert [record for record in caplog.records if "NON_EXISTENT_VAR" in record.message] == []


class _ConfigWithRatio(SincproConfig):
    """A numeric field with bounds — the shape of otlp_traces_sample_rate."""

    ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0


def build_ratio_config(monkeypatch, env_value: str):
    monkeypatch.setenv("TEST_RATIO", env_value)
    return build_config_obj(_ConfigWithRatio, CONFIG_WITH_RATIO)


def test_numeric_env_var_is_coerced(monkeypatch):
    """A sampling rate arrives as a string and must land as a float."""
    assert build_ratio_config(monkeypatch, "0.1").ratio == 0.1


@pytest.mark.parametrize("env_value", ["", "abc", "10%", "2.0", "-1"])
def test_an_unusable_env_var_falls_back_instead_of_killing_the_import(
    monkeypatch, caplog, env_value
):
    """`settings` is built at import time: a typo in one deployment variable
    must not make the whole framework unimportable."""
    with caplog.at_level(logging.INFO, logger="sincpro_framework"):
        config = build_ratio_config(monkeypatch, env_value)

    assert config.ratio == 1.0
    assert any("TEST_RATIO" in record.message for record in caplog.records)
    assert all(record.levelno < logging.WARNING for record in caplog.records)


def test_sample_rate_reads_the_standard_otel_variable():
    """Ops configures sampling with OTel's own variable, not a sincpro-only one."""
    from pathlib import Path

    conf = (
        Path(__file__).resolve().parents[2]
        / "sincpro_framework"
        / "conf"
        / "sincpro_framework_conf.yml"
    )
    assert "otlp_traces_sample_rate: $ENV:OTEL_TRACES_SAMPLER_ARG" in conf.read_text()


def test_the_framework_log_level_comes_from_the_environment():
    """A service sets its own level without reaching into framework internals.

    It used to be a literal in the conf, so the only way to change it was to assign
    `framework_settings.sincpro_framework_log_level` from outside — a side channel
    that also ran too late, since the logger is configured at import time.
    """
    from pathlib import Path

    conf = (
        Path(__file__).resolve().parents[2]
        / "sincpro_framework"
        / "conf"
        / "sincpro_framework_conf.yml"
    )
    assert "sincpro_framework_log_level: $ENV:SINCPRO_FRAMEWORK_LOG_LEVEL" in conf.read_text()
