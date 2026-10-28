"""ObjectStorage: objects stored by key on any store, and published by an id — for a time, for a number
of uses, to read or to write. No URL, no entrypoint, no owner: a key is the caller's, the URL the
entrypoint's, and `ObjectStorage` is injected as any adapter.

    from sincpro_framework.data_layer.object_storage import Access, ObjectStorage, S3CompatibleObjectStore

    billing.add_dependency("storage", ObjectStorage(S3CompatibleObjectStore("documents", boto3_client), secret))

    self.storage.put("exports/invoices.csv", data, "text/csv")
    self.storage.get("exports/invoices.csv")           whole, to process it
    self.storage.stream("exports/invoices.csv")        by parts, to pass it on
    self.storage.fetch(url, "inbox/scan.pdf", max_bytes=50 * MB)   a URL's bytes into the store
    published = self.storage.publish("exports/invoices.csv", Access.READ, timedelta(hours=1), max_uses=1)
    self.storage.redeem(published.id, Access.READ)     the entrypoint, when the id comes back

    domain/      ObjectStore (the port), ObjectRef, StoredObject, ObjectStream, Access, Publication, Grant, Refusal;
                 ObjectNotFound, ObjectAlreadyExists, PublicationRefused, FetchRefused, FetchFailed,
                 FetchRefusal
    adapters/    InMemoryObjectStore, LocalObjectStore, S3CompatibleObjectStore
    entrypoint/  ObjectStorage, the facade a project injects
"""

from sincpro_framework.data_layer.object_storage.adapters.in_memory import InMemoryObjectStore
from sincpro_framework.data_layer.object_storage.adapters.local import LocalObjectStore
from sincpro_framework.data_layer.object_storage.adapters.s3_compatible import (
    S3CompatibleObjectStore,
)
from sincpro_framework.data_layer.object_storage.domain.exceptions import (
    FetchFailed,
    FetchRefused,
    ObjectAlreadyExists,
    ObjectNotFound,
    PublicationRefused,
)
from sincpro_framework.data_layer.object_storage.domain.fetch import FetchRefusal
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectRef,
    ObjectStore,
    ObjectStream,
    StoredObject,
)
from sincpro_framework.data_layer.object_storage.domain.publication import (
    Grant,
    Publication,
    Refusal,
)
from sincpro_framework.data_layer.object_storage.entrypoint.object_storage import (
    ObjectStorage,
)

__all__ = [
    "Access",
    "FetchFailed",
    "FetchRefusal",
    "FetchRefused",
    "Grant",
    "InMemoryObjectStore",
    "LocalObjectStore",
    "ObjectAlreadyExists",
    "ObjectNotFound",
    "ObjectRef",
    "ObjectStorage",
    "ObjectStore",
    "ObjectStream",
    "Publication",
    "PublicationRefused",
    "Refusal",
    "S3CompatibleObjectStore",
    "StoredObject",
]
