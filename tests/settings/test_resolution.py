"""One document, any shape, one resolution — the three use cases of PRD_10 on one file.

The document is a project's one YAML file; a shape is any `SincproConfig` class. The global shape
sees everything, a context's shape sees the shared settings plus its own and nothing of the other
contexts, and any other shape — a subset, one inheriting everything — resolves by the same rule.
"""

import logging
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ConfigDict, ValidationError

from sincpro_framework.settings import Secret
from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj

DOCUMENT = """
sincpro_payments_sdk:
  environment: TEST
  log_level: INFO
  qr:
    linkser_endpoint: $ENV:LINKSER_ENDPOINT
  cybersource:
    merchant_id: M-1
    api_secret: $ENV:CYBERSOURCE_API_SECRET
    environment: PROD
  bank_account:
    bank: BNB
another_project:
  log_level: DEBUG
"""


class Environment(StrEnum):
    TEST = "TEST"
    PROD = "PROD"


class SharedSettings(SincproConfig):
    environment: Environment = Environment.TEST
    log_level: str = "INFO"


class QRSettings(SharedSettings):
    linkser_endpoint: str = "https://api.linkser.com"
    timeout: float = 10.0


class CybersourceSettings(SharedSettings):
    merchant_id: str = ""
    api_secret: Secret[str]
    timeout: float = 30.0


class BankAccountSettings(SharedSettings):
    bank: str = ""


class PaymentsSettings(SharedSettings):
    qr: QRSettings
    cybersource: CybersourceSettings
    bank_account: BankAccountSettings


class Everything(PaymentsSettings):
    report_bucket: str = "reports"


class Collections(SharedSettings):
    qr: QRSettings
    bank_account: BankAccountSettings


@pytest.fixture
def document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LINKSER_ENDPOINT", "https://linkser.example")
    monkeypatch.setenv("CYBERSOURCE_API_SECRET", "s3cr3t")
    path = tmp_path / "payments.yml"
    path.write_text(DOCUMENT)
    return str(path)


# ---------------------------------------------------------------------------------------------
# The three use cases
# ---------------------------------------------------------------------------------------------


def test_the_global_shape_sees_everything(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    assert settings.environment == "TEST"
    assert settings.qr.linkser_endpoint == "https://linkser.example"
    assert settings.cybersource.merchant_id == "M-1"
    assert settings.bank_account.bank == "BNB"


def test_a_section_inherits_what_it_does_not_set_from_the_nearest_section_above(
    document: str,
):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    assert settings.qr.environment == "TEST"
    assert settings.qr.log_level == "INFO"


def test_a_section_overrides_what_it_inherits(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    assert settings.cybersource.environment == "PROD"
    assert settings.environment == "TEST"


def test_a_context_shape_sees_the_shared_settings_and_its_own_and_nothing_else(document: str):
    qr = build_config_obj(QRSettings, document, "sincpro_payments_sdk.qr")

    assert qr.linkser_endpoint == "https://linkser.example"
    assert qr.environment == "TEST"
    assert not hasattr(qr, "merchant_id")
    assert not hasattr(qr, "cybersource")


def test_a_context_resolved_alone_equals_its_section_of_the_global(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    alone = build_config_obj(QRSettings, document, "sincpro_payments_sdk.qr")

    assert alone.model_dump() == settings.qr.model_dump()
    assert alone is not settings.qr


def test_any_shape_resolves_by_the_same_rule(document: str):
    everything = build_config_obj(Everything, document, "sincpro_payments_sdk")
    subset = build_config_obj(Collections, document, "sincpro_payments_sdk")

    assert (
        everything.report_bucket == "reports" and everything.cybersource.merchant_id == "M-1"
    )
    assert subset.qr.environment == "TEST" and subset.bank_account.bank == "BNB"
    assert not hasattr(subset, "cybersource")


def test_the_section_of_the_global_is_one_object():
    assert PaymentsSettings.model_fields["qr"].annotation is QRSettings


def test_a_section_missing_from_the_document_is_refused_by_its_path(document: str):
    with pytest.raises(ValueError, match="sincpro_payments_sdk.nothing not found"):
        build_config_obj(QRSettings, document, "sincpro_payments_sdk.nothing")


# ---------------------------------------------------------------------------------------------
# Every problem at once, by path
# ---------------------------------------------------------------------------------------------


def test_every_required_and_missing_setting_is_one_error_naming_each_path(tmp_path: Path):
    path = tmp_path / "broken.yml"
    path.write_text(
        "sincpro_payments_sdk:\n"
        "  qr: {}\n"
        "  cybersource:\n"
        "    api_secret: $ENV:NOT_SET_ANYWHERE\n"
        "  bank_account: {}\n"
    )

    class NeedsTwo(SharedSettings):
        cybersource: CybersourceSettings
        bank_account: BankAccountSettings
        signer_url: str

    with pytest.raises(ValidationError) as refused:
        build_config_obj(NeedsTwo, str(path), "sincpro_payments_sdk")

    locations = {error["loc"] for error in refused.value.errors()}
    assert ("cybersource", "api_secret") in locations and ("signer_url",) in locations


# ---------------------------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------------------------


def test_a_secret_is_masked_and_read_on_purpose(document: str):
    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    assert "s3cr3t" not in repr(settings)
    assert "s3cr3t" not in str(settings.cybersource)
    assert settings.cybersource.api_secret.get_secret_value() == "s3cr3t"


def test_a_secret_written_in_the_file_works_and_is_warned_about(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "literal.yml"
    path.write_text("root:\n  merchant_id: M\n  api_secret: in-the-file\n")

    with caplog.at_level(logging.WARNING, logger="sincpro_framework"):
        cybersource = build_config_obj(CybersourceSettings, str(path), "root")

    assert cybersource.api_secret.get_secret_value() == "in-the-file"
    assert any("api_secret" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------------------------
# The environment by path — only behind a declared prefix
# ---------------------------------------------------------------------------------------------


class PrefixedShared(SincproConfig):
    env_prefix: ClassVar[str] = "PAYMENTS"
    environment: Environment = Environment.TEST


class PrefixedQR(PrefixedShared):
    timeout: float = 10.0


class PrefixedPayments(PrefixedShared):
    qr: PrefixedQR


def test_the_environment_by_path_wins_over_the_file(document: str, monkeypatch):
    monkeypatch.setenv("PAYMENTS__QR__TIMEOUT", "5")

    settings = build_config_obj(PrefixedPayments, document, "sincpro_payments_sdk")

    assert settings.qr.timeout == 5.0


def test_an_environment_value_at_the_top_cascades_into_the_sections(
    document: str, monkeypatch
):
    monkeypatch.setenv("PAYMENTS__ENVIRONMENT", "PROD")

    settings = build_config_obj(PrefixedPayments, document, "sincpro_payments_sdk")
    alone = build_config_obj(PrefixedQR, document, "sincpro_payments_sdk.qr")

    assert settings.environment == "PROD" and settings.qr.environment == "PROD"
    assert alone.environment == "PROD"


def test_without_a_prefix_a_stray_path_variable_changes_nothing(document: str, monkeypatch):
    monkeypatch.setenv("PAYMENTS__QR__TIMEOUT", "5")
    monkeypatch.setenv("QR__TIMEOUT", "6")
    monkeypatch.setenv("SINCPRO_PAYMENTS_SDK__QR__TIMEOUT", "7")

    settings = build_config_obj(PaymentsSettings, document, "sincpro_payments_sdk")

    assert settings.qr.timeout == 10.0


def test_an_unusable_value_by_path_falls_back_to_the_default_as_a_sentinel_does(
    document: str, monkeypatch
):
    monkeypatch.setenv("PAYMENTS__QR__TIMEOUT", "not-a-number")

    settings = build_config_obj(PrefixedPayments, document, "sincpro_payments_sdk")

    assert settings.qr.timeout == 10.0


# ---------------------------------------------------------------------------------------------
# A flat class — exactly as today
# ---------------------------------------------------------------------------------------------


class FlatConfig(SincproConfig):
    log_level: str = "INFO"
    timeout: float = 1.0


def test_a_flat_class_takes_nothing_from_the_document_above_its_section(tmp_path: Path):
    """No cascade at the top: a key beside the project's section is another project's."""
    path = tmp_path / "flat.yml"
    path.write_text("timeout: 99\nmy_project:\n  log_level: DEBUG\n")

    flat = build_config_obj(FlatConfig, str(path), "my_project")

    assert flat.log_level == "DEBUG" and flat.timeout == 1.0


def test_a_flat_class_stays_assignable(document: str):
    flat = build_config_obj(FlatConfig, document, "another_project")

    flat.log_level = "INFO"

    assert flat.log_level == "INFO"


def test_a_shape_that_asks_to_be_frozen_is(document: str):
    class FrozenQR(QRSettings):
        model_config = ConfigDict(frozen=True)

    frozen = build_config_obj(FrozenQR, document, "sincpro_payments_sdk.qr")

    with pytest.raises(ValidationError):
        frozen.timeout = 1.0


class PostgresConf(SincproConfig):
    host: str = "localhost"
    port: int = 5432
    user: str = "my_user"


class ReadmeConfig(SincproConfig):
    """The class the README has always shown: a nested shape with a default, no `sub_key`."""

    log_level: str = "DEBUG"
    token: str = "default_my_token"
    postgresql: PostgresConf = PostgresConf()


def test_the_readme_nested_class_reads_its_section_as_always(tmp_path: Path):
    path = tmp_path / "readme.yml"
    path.write_text("log_level: INFO\npostgresql:\n  host: db\n  port: 12345\n")

    config = build_config_obj(ReadmeConfig, str(path))

    assert config.log_level == "INFO"
    assert config.postgresql == PostgresConf(host="db", port=12345)


def test_a_nested_shape_with_a_default_and_no_section_keeps_its_default(tmp_path: Path):
    class Required(SincproConfig):
        dsn: str

    class WithDefaults(SincproConfig):
        postgresql: PostgresConf = PostgresConf(host="from-the-default")
        required: Required = Required(dsn="given")

    path = tmp_path / "empty.yml"
    path.write_text("my_project:\n  other: 1\n")

    config = build_config_obj(WithDefaults, str(path), "my_project")

    assert config.postgresql.host == "from-the-default" and config.required.dsn == "given"


def test_a_built_object_equals_one_written_by_hand_with_the_same_values(document: str):
    built = build_config_obj(FlatConfig, document, "another_project")

    assert built == FlatConfig(log_level="DEBUG")
    assert built != FlatConfig(log_level="INFO")


def test_a_frozen_shape_is_still_hashable(document: str):
    class FrozenQR(QRSettings):
        model_config = ConfigDict(frozen=True)

    frozen = build_config_obj(FrozenQR, document, "sincpro_payments_sdk.qr")

    assert hash(frozen) == hash(frozen)


def test_a_field_a_nested_shape_declares_itself_is_never_taken_from_above(tmp_path: Path):
    """Only what a shape inherits — the shared settings — cascades: a nested class written before
    the cascade, sharing a field name with its parent by chance, builds as it always did."""

    class Own(SincproConfig):
        log_level: str = "nested-default"

    class Parent(SincproConfig):
        log_level: str = "DEBUG"
        own: Own = Own()

    path = tmp_path / "own.yml"
    path.write_text("p:\n  log_level: INFO\n  own:\n    other: 1\n")

    assert build_config_obj(Parent, str(path), "p").own.log_level == "nested-default"


def test_a_context_that_redeclares_a_shared_field_owns_it(document: str):
    class OwnEnvironmentQR(SharedSettings):
        environment: Environment = Environment.PROD
        timeout: float = 10.0

    class Payments(SharedSettings):
        qr: OwnEnvironmentQR

    settings = build_config_obj(Payments, document, "sincpro_payments_sdk")

    assert settings.environment == "TEST" and settings.qr.environment == "PROD"
