"""`SealedJsonCodec`: a token or a grant as the bytes a shared store holds — its secrets' real
values, which pydantic's JSON would mask — encrypted when given a `Sealer`.

    SealedJsonCodec(TokenResponse, "issued-token-1", sealer=AesGcmSealer(key))
    SealedJsonCodec(OAuthGrant, "oauth-grant-1")

Context: the schema names the layout, and an encrypted one is another — so neither is ever read
as the other. A value that does not decrypt or parse raises: a cache counts that as a miss.
"""

import json

from pydantic import BaseModel, SecretStr

from sincpro_framework.auth.client.domain.outbound import Sealer


class SealedJsonCodec[T: BaseModel]:
    """A pydantic model as JSON with its `SecretStr` fields revealed, then encrypted."""

    def __init__(self, model: type[T], layout: str, sealer: Sealer | None = None) -> None:
        """`layout` names the shape — part of the schema, so a change of shape reads none of
        the old values."""
        self.model = model
        self.layout = layout
        self.sealer = sealer

    @property
    def schema(self) -> str:
        """The layout's name — an encrypted one is another."""
        return self.layout + ("-sealed" if self.sealer else "")

    def encode(self, value: T) -> bytes:
        """The model as JSON, its secrets in clear — then encrypted, when a sealer was given."""
        fields = value.model_dump(mode="json")
        for name, field in value:
            if isinstance(field, SecretStr):
                fields[name] = field.get_secret_value()
        plain = json.dumps(fields).encode()
        return self.sealer.encrypt(plain) if self.sealer else plain

    def decode(self, raw: bytes) -> T:
        """The model back — decrypted first when it was encrypted."""
        plain = self.sealer.decrypt(raw) if self.sealer else raw
        return self.model.model_validate_json(plain)
