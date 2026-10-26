"""`ParameterStore` and `SecretsManager`: settings read from AWS, by their own sentinels — a
`SettingSource` each, behind the `[aws]` extra.

    token: $SSM:/billing/prod/token                 # Systems Manager Parameter Store
    database: $SECRET:billing/prod/db#password       # Secrets Manager — a key of its JSON

    build_config_obj(Billing, "conf/billing.yml", "billing",
                     sources={"SSM": ParameterStore(), "SECRET": SecretsManager()})

Context: each value is asked once per source object and stored — settings are built when the
process starts, and a value that changes is read by building again. The client is made at the
first read, so a source handed in where there is no boto3 or no credentials costs nothing until
it is asked. A name the service does not hold answers `None`: the field falls back to its
default, as an unset variable does. Any other error — no boto3, no permission, no network — is
raised to the build, which says it in a warning and leaves the value as written: settings never
stop a process from starting.
"""

import json
from typing import Any

from sincpro_framework.common.aws import aws_client

NOT_FOUND = {"ParameterNotFound", "ResourceNotFoundException"}


def _missing(error: Exception) -> bool:
    """Whether a boto3 error says the name is not held — a `ClientError` whose code is one of
    `NOT_FOUND`; any other error, or one with no `response`, is not."""
    response = getattr(error, "response", None) or {}
    return response.get("Error", {}).get("Code") in NOT_FOUND


class ParameterStore:
    """A `SettingSource` over Systems Manager Parameter Store — each name read once and stored.

    ParameterStore(prefix="/billing/prod")("token")   ->  "s3cr3t"   # /billing/prod/token
    ParameterStore()("/not/there")                    ->  None
    """

    service = "ssm"

    def __init__(self, client: Any = None, prefix: str = "") -> None:
        """`client` is a boto3 SSM client — boto3's default when none is given; `prefix` is put
        before every name (`/billing/prod`), so the file says only `$SSM:token`."""
        self._client = client
        self.prefix = prefix
        self._read: dict[str, str | None] = {}

    @property
    def client(self) -> Any:
        """The boto3 client, made at the first read when none was handed in."""
        if self._client is None:
            self._client = aws_client(self.service)
        return self._client

    def __call__(self, name: str) -> str | None:
        """The parameter's value, decrypted when it is a `SecureString`."""
        if name not in self._read:
            try:
                answer = self.client.get_parameter(
                    Name=self.prefix + name, WithDecryption=True
                )
                self._read[name] = answer["Parameter"]["Value"]
            except Exception as error:
                if not _missing(error):
                    raise
                self._read[name] = None
        return self._read[name]


class SecretsManager:
    """A `SettingSource` over Secrets Manager — each secret read once and stored, whole or one
    key of its JSON.

        SecretsManager()("billing/db")            ->  '{"user": "app", "password": "…"}'
        SecretsManager()("billing/db#password")   ->  "…"
    """

    service = "secretsmanager"

    def __init__(self, client: Any = None) -> None:
        """`client` is a boto3 Secrets Manager client — boto3's default when none is given."""
        self._client = client
        self._read: dict[str, str | None] = {}

    @property
    def client(self) -> Any:
        """The boto3 client, made at the first read when none was handed in."""
        if self._client is None:
            self._client = aws_client(self.service)
        return self._client

    def _secret(self, secret_id: str) -> str | None:
        """The secret's `SecretString`, asked once per id — `None` when the service does not
        hold it, or when it holds only a binary secret."""
        if secret_id not in self._read:
            try:
                answer = self.client.get_secret_value(SecretId=secret_id)
                self._read[secret_id] = answer.get("SecretString")
            except Exception as error:
                if not _missing(error):
                    raise
                self._read[secret_id] = None
        return self._read[secret_id]

    def __call__(self, name: str) -> str | None:
        """`billing/db` is the whole secret; `billing/db#password` one key of its JSON."""
        secret_id, _, key = name.partition("#")
        secret = self._secret(secret_id)
        if secret is None or not key:
            return secret
        value = json.loads(secret).get(key)
        return None if value is None else str(value)
