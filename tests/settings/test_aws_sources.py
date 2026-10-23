"""Settings read from AWS Systems Manager Parameter Store and Secrets Manager, by their own
sentinels — on moto, so no account is needed."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from sincpro_framework.settings import Secret  # noqa: E402
from sincpro_framework.settings.adapters.aws import (  # noqa: E402
    ParameterStore,
    SecretsManager,
)
from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj  # noqa: E402

DOCUMENT = """
billing:
  token: $SSM:token
  timeout: $SSM:timeout
  password: $SECRET:billing/db#password
  dsn: $SECRET:billing/dsn
  region: $SSM:not-there
"""


class Billing(SincproConfig):
    token: Secret[str]
    timeout: int = 30
    password: Secret[str] = Secret("")
    dsn: str = ""
    region: str = "us-east-1"


@pytest.fixture(autouse=True)
def aws(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        yield


def test_a_shape_reads_parameters_and_secrets(tmp_path: Path):
    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/billing/prod/token", Value="t-1", Type="SecureString")
    ssm.put_parameter(Name="/billing/prod/timeout", Value="45", Type="String")
    secrets = boto3.client("secretsmanager")
    secrets.create_secret(Name="billing/db", SecretString=json.dumps({"password": "p-1"}))
    secrets.create_secret(Name="billing/dsn", SecretString="pg://db")
    path = tmp_path / "billing.yml"
    path.write_text(DOCUMENT)

    built = build_config_obj(
        Billing,
        str(path),
        "billing",
        sources={"SSM": ParameterStore(prefix="/billing/prod/"), "SECRET": SecretsManager()},
    )

    assert built.token.get_secret_value() == "t-1"
    assert built.password.get_secret_value() == "p-1"
    assert (built.timeout, built.dsn, built.region) == (45, "pg://db", "us-east-1")


def test_a_name_the_store_does_not_hold_is_none_and_read_once():
    store = ParameterStore()
    asked: list[str] = []
    original = store.client.get_parameter

    def counted(**arguments):
        asked.append(arguments["Name"])
        return original(**arguments)

    store.client.get_parameter = counted

    assert store("/missing") is None and store("/missing") is None
    assert asked == ["/missing"]
