# Object storage: objects by key on any store, published by an id

A use case stores an object — an export, a scan, a report, a video — and makes it reachable: for an
hour, once, to read it or to upload it. `ObjectStorage` does both over any `ObjectStore` — a bucket
of an object storage, a directory emulating one, the process's memory — injected as any adapter.

It knows no URL and no owner. A key is the caller's, built from whatever the caller knows; a
publication is an **id** with its rules — how long, how many uses, to read or to write — and the
entrypoint that exposes it puts the id in its URL, its message or its answer, and asks `redeem`
when the id comes back.

Object storage is not a filesystem: no directories to walk, no permissions, no locks — objects by
key. A filesystem (NFS, SMB, a mounted disk) is another technology, and another component.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Objects by key

```python
from datetime import timedelta

from sincpro_framework.data_layer.object_storage import InMemoryObjectStore, ObjectAlreadyExists, ObjectStorage

storage = ObjectStorage(InMemoryObjectStore(), "the-service-secret")

stored = storage.put("exports/invoices.csv", b"number,total\nF-1,100\n", "text/csv", {"filename": "facturas.csv"})
assert (stored.size, stored.metadata) == (21, {"filename": "facturas.csv"})

assert storage.get("exports/invoices.csv").startswith(b"number")
opened = storage.open("exports/invoices.csv")                # the bytes and what they are, in one read
assert (opened.ref.content_type, opened.ref.metadata["filename"]) == ("text/csv", "facturas.csv")
assert storage.exists("exports/invoices.csv")
assert list(storage.keys("exports/")) == ["exports/invoices.csv"]

storage.put("claims/upload-7", b"", if_absent=True)          # only when nothing is there
try:
    storage.put("claims/upload-7", b"", if_absent=True)
except ObjectAlreadyExists:
    pass                                                    # another writer came first

storage.put("inbox/scan.pdf", b"%PDF-1.7", metadata={"filename": "scan.pdf"}, expires_in=timedelta(hours=1))
taken = storage.take("inbox/scan.pdf")                      # read and deleted, once on any replica
assert taken.data == b"%PDF-1.7" and not storage.exists("inbox/scan.pdf")
```

- **`put` / `open` / `get` / `find` / `exists` / `take` / `delete` / `keys(prefix)`**. A key with
  nothing under it is `ObjectNotFound` (`not_found`) from `open`, `get` and `take`, `None` from
  `find`.
- **`open`** answers a `StoredObject` — `data` and its `ref` — in one read of the store: a bucket's
  `get_object` already says what the object is.
- **`take`** reads and deletes, once: it claims the key in the `KeyValueStore` the context stands on
  (`add`, a `SET NX` on Redis or Valkey) before it reads, so of two replicas taking the same object
  one gets it and the other `ObjectNotFound` — on any storage, whatever it offers. A claim lasts a
  minute at most: a process that dies while taking never locks the object for good.
- **`expires_in`** makes an object gone after its time: `find`, `open`, `get`, `take` and `exists`
  answer it as not there, and delete it. Memory and disk drop what expired as they list; a bucket's
  listing does not read the expiry, so `keys` may name one until a lifecycle rule cleans it.
- **`data` is bytes or a file-like** — a request body spooled to disk — streamed to the store,
  never all in memory where the store streams (a bucket does).
- **Metadata values are any text**: a bucket carries them as headers, so the adapter encodes what
  is not ASCII and reads it back as written. **Metadata names** are lowercase letters, digits and
  `-` — anything else is a `ProgrammingError` on every store, memory included: AWS lowercases
  names and Ceph RGW (DigitalOcean Spaces) answers a `_` as `-`, so a name like `file_name` would
  come back as another one in production while every test passed.
- **A content type not given** is the one the key's extension says (`.csv` → `text/csv`), else
  `application/octet-stream`.
- **`ObjectRef`** is an object without its bytes: key, size, content type, the `metadata` stored
  beside it, when it was last put (`modified_at`) and when it expires (`expires_at`).
- **`if_absent=True`** writes only under a free key, atomically — `ObjectAlreadyExists`
  (`conflict`) otherwise. What a claim needs: of two writers at once, one stores.
- A failure of the storage — a bucket down, credentials refused — is an `ExternalServiceError`.

## Publishing: an id, its time, its uses

```python
from datetime import timedelta

from sincpro_framework.data_layer.object_storage import Access, PublicationRefused, Refusal

published = storage.publish(
    "exports/invoices.csv",
    Access.READ,                      # Access.WRITE: whoever holds the id uploads under the key
    expires_in=timedelta(hours=1),
    max_uses=1,                       # None: every time while it lives
    metadata={"filename": "facturas.csv"},
)

assert storage.check(published.id, Access.READ).uses == 0     # nothing spent: a preview, a HEAD
opened = storage.redeem(published.id, Access.READ)            # one use spent, atomically
assert (opened.key, opened.uses) == ("exports/invoices.csv", 1)

try:
    storage.redeem(published.id, Access.READ)
except PublicationRefused as refused:
    assert refused.reason is Refusal.USED_UP
    assert refused.publication.key == "exports/invoices.csv"
```

`Publication` — `id`, `key`, `access`, `expires_at`, `max_uses`, `uses`, `metadata`, `then` — is
what the publisher hands on and what `check` and `redeem` answer. When an id does not open,
`PublicationRefused` says why in `reason`, and which one in `publication` when it is known
(`EXPIRED`, `USED_UP`, `WRONG_ACCESS`) — its key and metadata, to answer "already uploaded:
scan.pdf" or to go on with its object. Every wire of the framework answers `UNKNOWN` as
`not_found` and the rest as `permission_denied`:

| `Refusal` | When |
|---|---|
| `FORGED` | the id was not signed by this service — no store is asked |
| `UNKNOWN` | no publication has it: never made, revoked (`storage.revoke(id)`), or gone with the store's time |
| `EXPIRED` | its `expires_at` passed |
| `USED_UP` | it was redeemed `max_uses` times |
| `WRONG_ACCESS` | published to read and asked to write, or the other way |

**The id.** With a secret, the id is 128 random bits and their HMAC-SHA256 cut to 128 bits — 55
characters, short enough to copy: an id not signed here is refused before any store is asked.
Without one, the random bits alone, refused when no publication has them.

**Where a publication lives.** On the `KeyValueStore` the context stands on — the bus in play's,
else the process's (`store_context_in(...)`), else this process's memory. A service whose context is
stored in Redis or Valkey shares its publications across every replica with nothing else to set. It
is written once and lasts its own time and a day more — to answer a late caller `EXPIRED` or
`USED_UP`, never `UNKNOWN`. Its uses are a counter (`increment`): a use is spent in one atomic step,
with nothing read to compare and nothing retried, so of two replicas redeeming the last use one
opens it, and a download used without end counts every use without a conflict.

**Publish after the context is stored.** A publication goes to the store in play *when it is
published*: one published before `store_context_in(...)` runs stays in this process's memory, and a
route on another replica — or after the store is set — answers `UNKNOWN`.

## Two accesses, one id: upload, then claim

An upload the client sends now and a use case claims later — once a person approves a preview — is
one publication: its first access spent, it opens to `then`, with the same id, for its own time from
that moment and its own uses.

```python
from sincpro_framework.data_layer.object_storage import Grant

ticket = storage.publish(
    "inbox/contract.pdf",
    Access.WRITE,
    timedelta(minutes=5),                                     # the link to upload: short
    max_uses=1,
    metadata={"filename": "contract.pdf"},
    then=Grant(access=Access.READ, expires_in=timedelta(hours=1), max_uses=1),   # the upload waits
)

storage.redeem(ticket.id, Access.WRITE)                       # the route of the upload
storage.put(ticket.key, b"%PDF-1.7", expires_in=timedelta(hours=1))

preview = storage.check(ticket.id, Access.READ)               # a preview: nothing spent
assert preview.metadata["filename"] == "contract.pdf"

claimed = storage.redeem(ticket.id, Access.READ)              # the confirmation: once
assert storage.take(claimed.key).data == b"%PDF-1.7"
```

Asking the first access again once it is spent is `USED_UP`; the `then` is `None` once it opened. A
`then` needs `max_uses` on the first access — without an end it would never open.

## The entrypoint's side

The URL is the entrypoint's: its host, its route, its headers. It publishes, puts the id where it
likes, and redeems the id when it comes back — any HTTP framework, a queue, a CLI:

```python
def download_route(id: str) -> tuple[int, bytes]:
    """`GET /files/{id}` of the host's own — 200 with the bytes, or what the refusal says."""
    try:
        opened = storage.redeem(id, Access.READ)
    except PublicationRefused as refused:
        return (410 if refused.reason in (Refusal.EXPIRED, Refusal.USED_UP) else 404), b""
    return 200, storage.get(opened.key)


link = storage.publish("exports/invoices.csv", max_uses=1)
url = f"https://api.acme.bo/files/{link.id}"          # what the caller is handed
assert download_route(link.id)[0] == 200
assert download_route(link.id)[0] == 410


def upload_route(id: str, body: bytes, content_type: str) -> int:
    """`PUT /uploads/{id}` — the client sends the bytes, the id says where they go."""
    try:
        opened = storage.redeem(id, Access.WRITE)
    except PublicationRefused:
        return 403
    storage.put(opened.key, body, content_type, {"filename": opened.metadata.get("filename", "")})
    return 201


ticket = storage.publish("inbox/scan.pdf", Access.WRITE, timedelta(minutes=5), max_uses=1, metadata={"filename": "scan.pdf"})
assert upload_route(ticket.id, b"%PDF-1.7", "application/pdf") == 201
assert storage.find("inbox/scan.pdf").metadata == {"filename": "scan.pdf"}
```

## The stores

| Store | For |
|---|---|
| `InMemoryObjectStore()` | tests, one replica |
| `LocalObjectStore("/data/objects")` | a volume the service mounts; a key never leaves the directory, the metadata is a JSON file beside each object, `if_absent` an exclusive create |
| `S3CompatibleObjectStore("bucket", client, prefix="", put_options={})` | any object storage on the S3 API — AWS, DigitalOcean Spaces, MinIO, Cloudflare R2, Wasabi; `if_absent` a conditional write |
| a project's own | inherit `ObjectStore`; prove it with `ObjectStoreContract` |

**A bucket is configured the way the industry does.** The client is the project's, built with
everything boto3 takes — credentials, region, `endpoint_url`, addressing style, signature version,
timeouts, retries (`botocore.config.Config`). What is set once per bucket is the adapter's: the
`prefix` every key lives under, and the `put_options` every put carries — `ServerSideEncryption`,
`SSEKMSKeyId`, `StorageClass`, `ACL`, `CacheControl`, `Tagging` — with each call's own options over
them:

```python
import boto3
from botocore.config import Config

from sincpro_framework.data_layer.object_storage import S3CompatibleObjectStore

client = boto3.client(
    "s3",
    region_name="nyc3",
    endpoint_url="https://nyc3.digitaloceanspaces.com",
    aws_access_key_id="the-key",
    aws_secret_access_key="the-secret",
    config=Config(signature_version="s3v4", retries={"max_attempts": 3}, connect_timeout=2),
)
s3_store = S3CompatibleObjectStore(
    "documents",
    client,
    prefix="billing/",
    put_options={"ServerSideEncryption": "AES256", "StorageClass": "STANDARD_IA"},
)
assert s3_store.presign("a.pdf", Access.READ, timedelta(minutes=5)).startswith(
    "https://nyc3.digitaloceanspaces.com/documents/billing/a.pdf?"
)
```

```python
from collections.abc import Iterator, Mapping
from datetime import datetime

from sincpro_framework.data_layer.object_storage import ObjectRef, ObjectStore, StoredObject


class TableObjectStore(ObjectStore):
    """A project's own: objects in a table."""

    def __init__(self) -> None:
        self.rows: dict[str, StoredObject] = {}

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
        if_absent: bool,
        expires_at: datetime | None,
    ) -> bool:
        if if_absent and key in self.rows:
            return False
        said = ObjectRef(key=key, size=len(data), content_type=content_type, metadata=dict(metadata), expires_at=expires_at)
        self.rows[key] = StoredObject(data=data, ref=said)
        return True

    def get(self, key: str) -> StoredObject | None:
        return self.rows.get(key)

    def info(self, key: str) -> ObjectRef | None:
        return self.rows[key].ref if key in self.rows else None

    def delete(self, key: str) -> None:
        self.rows.pop(key, None)

    def keys(self, prefix: str) -> Iterator[str]:
        return (key for key in sorted(self.rows) if key.startswith(prefix))


own = ObjectStorage(TableObjectStore())
own.put("a.txt", b"hello", "text/plain")
assert own.get("a.txt") == b"hello"
```

## Making it yours

What `ObjectStorage` does is the recommended default, never the only way:

| To change | How |
|---|---|
| What the storage is told — headers, an ACL, a cache control, an encryption key, a version, a range | pass it on any call: `storage.put(key, data, type, CacheControl="max-age=3600")`, `storage.get(key, SSECustomerAlgorithm="AES256", SSECustomerKey=k)`, `storage.get(key, VersionId=v)`, `storage.delete(key, VersionId=v)` — options go to the storage as they are |
| The bucket serving the bytes itself | type the adapter: `s3_store.presign(key, Access.READ, ttl, ResponseContentDisposition="attachment")` — a presigned URL, the technology's own |
| Rules of its own — who may redeem, what happens after | put them in `metadata` at `publish`, read them from what `check` / `redeem` answer |
| Where publications and claims are saved | `ObjectStorage(store, secret, context_store=a_context_store)` |
| How ids are signed | a `PublicationIds` of its own (`signed` / `unsigned`) in `storage.ids` and `storage.publications.ids` |
| How a publication opens | a `Publications` of its own in `storage.publications` |
| Where the bytes live | an `ObjectStore` of its own, proven with `ObjectStoreContract` |

`ObjectStorage` is a facade and decides nothing: each call goes to the service of its use case, and
each is an attribute a project reads or replaces.

| Attribute | Service | Use case |
|---|---|---|
| `storage.objects` | `Objects` | put, open, get, find, exists, take, delete, keys — the rules over the store |
| `storage.ids` | `PublicationIds` | the id, signed with the secret |
| `storage.publications` | `Publications` | publish, check, redeem, revoke — on the context's `KeyValueStore` |
| `storage.store` | the `ObjectStore` | what only its technology offers — a bucket's `presign` |

```python
from sincpro_framework.context import InMemoryContexts

audited = ObjectStorage(InMemoryObjectStore(), "the-service-secret", context_store=InMemoryContexts())
share = audited.publish("contracts/7.pdf", metadata={"for_user": "ana"})

opened = audited.check(share.id, Access.READ)
assert opened.metadata["for_user"] == "ana"          # the entrypoint judges it by its own rule
```

## In a use case

`ObjectStorage` is a dependency like any adapter: `billing.add_dependency("storage", ObjectStorage(...))`,
and a Feature reads `self.storage`. The secret is the project's to hand in. Whose an object is — a
tenant, a user, nobody — is the project's to put in the key or in the metadata, if it wants.
