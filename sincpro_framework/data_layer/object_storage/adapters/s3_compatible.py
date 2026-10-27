"""`S3CompatibleObjectStore`: objects on a bucket of any object storage that speaks the S3 API — AWS,
DigitalOcean Spaces, MinIO, Cloudflare R2, Wasabi.

    S3CompatibleObjectStore("documents", boto3.client("s3", endpoint_url="https://nyc3.digitaloceanspaces.com"))
    S3CompatibleObjectStore("documents", client, prefix="billing/")           every key under a prefix
    S3CompatibleObjectStore("documents", client, put_options={"ServerSideEncryption": "AES256",
                            "StorageClass": "STANDARD_IA", "ACL": "private"})   on every put
    store.put(key, data, "application/pdf", {"by": "ana"}, False, CacheControl="max-age=3600")
    store.presign(key, Access.READ, timedelta(hours=1), ResponseContentDisposition="attachment")
        ->  "https://documents.s3…?X-Amz-Signature=…"   the client gets the bytes from the bucket

Context: the client is the project's — its credentials, region, endpoint, addressing style,
signature version, timeouts, retries (`botocore.config.Config`) — handed in built (`[aws]` brings
boto3); nothing is imported here. What the industry sets once per bucket is the adapter's: the
`prefix` every key lives under, and the `put_options` every put carries — encryption, storage
class, ACL, cache control, tagging — each call's options over them. Every option the API takes
passes as it is: `put`, `get`, `info`, `delete` and `keys` hand them to `put_object`,
`get_object`, `head_object`, `delete_object` and `list_objects_v2`, `presign` to the presigned
call's parameters — an SSE-C key, a `VersionId`, a `Range`.
`presign` is this technology's own, not the port's: a Feature that wants the bucket to serve the
bytes types this adapter. A file-like is the `Body` as it is, streamed by the client. Metadata
travels as HTTP headers, so each value is percent-encoded beyond printable ASCII and decoded back
(`"facturación.pdf"` reads as written). A put `if_absent` is a conditional write
(`IfNoneMatch="*"`). A
failure of the storage — not a key with nothing under it — is an `ExternalServiceError`.
"""

from collections.abc import Iterator, Mapping
from datetime import timedelta
from typing import Any, BinaryIO
from urllib.parse import quote, unquote

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectRef,
    ObjectStore,
)

MISSING = frozenset({"NoSuchKey", "404", "NotFound"})
"""The error codes an object storage answers for a key with nothing under it."""

HEADER_SAFE = "".join(chr(code) for code in range(0x20, 0x7F) if chr(code) != "%")
"""What a metadata value keeps as it is: printable ASCII but `%` — everything else, an accent or
an emoji, is percent-encoded, since the storage carries metadata as HTTP headers."""

TAKEN = frozenset({"PreconditionFailed", "412", "ConditionalRequestConflict", "409"})
"""The error codes a conditional write answers when another object is, or is being, written."""


def _code(error: Exception) -> str:
    """The error code a botocore error carries — `""` for any other error."""
    response = getattr(error, "response", None) or {}
    return str(response.get("Error", {}).get("Code", ""))


class S3CompatibleObjectStore(ObjectStore):
    """Objects on one bucket of an object storage, under an optional prefix."""

    def __init__(
        self,
        bucket: str,
        client: Any,
        prefix: str = "",
        put_options: Mapping[str, Any] | None = None,
    ) -> None:
        """`bucket` on `client` — a boto3 client of the S3 API — each key under `prefix`, every
        put with `put_options`."""
        self.bucket = bucket
        self.client = client
        self.prefix = prefix
        self.put_options = dict(put_options or {})

    def _failed(
        self, doing: str, key: str, error: Exception
    ) -> exceptions.ExternalServiceError:
        """The storage failed `doing` on `key`."""
        return exceptions.ExternalServiceError(
            f"the object storage failed to {doing} {key} on {self.bucket}: {error}"
        )

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str,
        metadata: Mapping[str, str],
        if_absent: bool,
        **options: Any,
    ) -> bool:
        conditions = {"IfNoneMatch": "*"} if if_absent else {}
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.prefix + key,
                Body=data,
                ContentType=content_type,
                **{
                    "Metadata": {
                        name: quote(value, safe=HEADER_SAFE)
                        for name, value in metadata.items()
                    },
                    **self.put_options,
                    **conditions,
                    **options,
                },
            )
        except Exception as error:
            if if_absent and _code(error) in TAKEN:
                return False
            raise self._failed("put", key, error) from error
        return True

    def get(self, key: str, **options: Any) -> bytes | None:
        try:
            answer = self.client.get_object(
                Bucket=self.bucket, Key=self.prefix + key, **options
            )
        except Exception as error:
            if _code(error) in MISSING:
                return None
            raise self._failed("get", key, error) from error
        return answer["Body"].read()

    def info(self, key: str, **options: Any) -> ObjectRef | None:
        try:
            answer = self.client.head_object(
                Bucket=self.bucket, Key=self.prefix + key, **options
            )
        except Exception as error:
            if _code(error) in MISSING:
                return None
            raise self._failed("read", key, error) from error
        return ObjectRef(
            key=key,
            size=int(answer.get("ContentLength", 0)),
            content_type=answer.get("ContentType") or "application/octet-stream",
            metadata={
                name: unquote(value) for name, value in (answer.get("Metadata") or {}).items()
            },
            modified_at=answer.get("LastModified"),
        )

    def delete(self, key: str, **options: Any) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key, **options)
        except Exception as error:
            if _code(error) not in MISSING:
                raise self._failed("delete", key, error) from error

    def keys(self, prefix: str, **options: Any) -> Iterator[str]:
        asked = {"Bucket": self.bucket, "Prefix": self.prefix + prefix, **options}
        while True:
            try:
                page = self.client.list_objects_v2(**asked)
            except Exception as error:
                raise self._failed("list", prefix, error) from error
            for found in page.get("Contents", []):
                yield found["Key"].removeprefix(self.prefix)
            if not page.get("IsTruncated"):
                return
            asked["ContinuationToken"] = page["NextContinuationToken"]

    def presign(self, key: str, access: Access, expires_in: timedelta, **options: Any) -> str:
        """A URL the storage signs: GET to read, PUT to write, for `expires_in` — `options`
        among its parameters (the `ContentType` a write must carry, the
        `ResponseContentDisposition` a read answers with)."""
        operation = "put_object" if access is Access.WRITE else "get_object"
        return self.client.generate_presigned_url(
            operation,
            Params={"Bucket": self.bucket, "Key": self.prefix + key, **options},
            ExpiresIn=int(expires_in.total_seconds()),
        )
