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
    self.storage.link("photos/01a1….jpg")              a link saved nowhere, its end signed
    self.storage.put_image("photos/01a1e.jpg", data, "Directorio.jpg")   the original and its sizes

    domain/      ObjectStore (the port), ObjectRef, StoredObject, ObjectStream, Access, Publication, Grant, Refusal;
                 ImageProcessor (the port), ImageSize, SIZES, ImagePut, Processed, Rendered, ImageRefusal;
                 ObjectNotFound, ObjectAlreadyExists, PublicationRefused, ImageRefused, FetchRefused,
                 FetchFailed, FetchRefusal
    adapters/    InMemoryObjectStore, LocalObjectStore, S3CompatibleObjectStore, PillowImages (the `images` extra)
    entrypoint/  ObjectStorage, the facade a project injects
"""

from sincpro_framework.data_layer.object_storage.adapters.in_memory import InMemoryObjectStore
from sincpro_framework.data_layer.object_storage.adapters.local import LocalObjectStore
from sincpro_framework.data_layer.object_storage.adapters.pillow_images import PillowImages
from sincpro_framework.data_layer.object_storage.adapters.s3_compatible import (
    S3CompatibleObjectStore,
)
from sincpro_framework.data_layer.object_storage.domain.exceptions import (
    FetchFailed,
    FetchRefused,
    ImageRefused,
    ObjectAlreadyExists,
    ObjectNotFound,
    PublicationRefused,
)
from sincpro_framework.data_layer.object_storage.domain.fetch import FetchRefusal
from sincpro_framework.data_layer.object_storage.domain.images import (
    SIZES,
    ImageProcessor,
    ImagePut,
    ImageRefusal,
    ImageSize,
    Processed,
    Rendered,
    rendition_key,
)
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
from sincpro_framework.data_layer.object_storage.services.links import Linked

__all__ = [
    "SIZES",
    "Access",
    "FetchFailed",
    "FetchRefusal",
    "FetchRefused",
    "Grant",
    "ImageProcessor",
    "ImagePut",
    "ImageRefusal",
    "ImageRefused",
    "ImageSize",
    "InMemoryObjectStore",
    "Linked",
    "LocalObjectStore",
    "ObjectAlreadyExists",
    "ObjectNotFound",
    "ObjectRef",
    "ObjectStorage",
    "ObjectStore",
    "ObjectStream",
    "PillowImages",
    "Processed",
    "Publication",
    "PublicationRefused",
    "Refusal",
    "Rendered",
    "S3CompatibleObjectStore",
    "StoredObject",
    "rendition_key",
]
