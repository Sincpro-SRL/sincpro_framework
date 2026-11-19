"""`Images`: a picture stored with its renditions — read and encoded by the project's `ImageProcessor`,
then the original as it came and each rendition beside it, or nothing at all.

    images = Images(objects, PillowImages())
    images.put("photos/01a1e.jpg", data, "Directorio 2026.jpg")
        ->  ImagePut(original=ObjectRef("photos/01a1e.jpg", metadata={"filename": …, "width": "4032", …}),
                     renditions={"thumb": ObjectRef("photos/01a1e@thumb.webp", …), "view": …})
    images.put("photos/scan.jpg", b"%PDF-1.7", "scan.jpg")   ->  ImageRefused(NOT_AN_IMAGE), nothing stored

Context: the picture is read before anything is written, so a refused one leaves nothing; once
read, the original and its renditions are written, and a store that fails midway has what it
took removed — a record never points at half a picture. The original keeps the type it was sent
in by what it IS (a PNG named `.jpg` is stored as a PNG), and its size in pixels.
"""

from collections.abc import Mapping, Sequence
from typing import BinaryIO

from sincpro_framework.data_layer.object_storage.domain.exceptions import ImageRefused
from sincpro_framework.data_layer.object_storage.domain.images import (
    ImageProcessor,
    ImagePut,
    ImageRefusal,
    ImageSize,
    rendition_key,
)
from sincpro_framework.data_layer.object_storage.domain.object_store import ObjectRef
from sincpro_framework.data_layer.object_storage.services.objects import Objects

MAX_IMAGE_BYTES = 20 * 1024 * 1024
"""Twenty megabytes: past a phone's photo, short of a scan nobody shows."""


def _read(data: bytes | BinaryIO, max_bytes: int) -> bytes:
    """The bytes of `data`, refused past `max_bytes` — a file-like read no further than that."""
    read = data if isinstance(data, bytes) else data.read(max_bytes + 1)
    if len(read) > max_bytes:
        raise ImageRefused(
            ImageRefusal.TOO_LARGE, f"a picture is taken up to {max_bytes} bytes"
        )
    return read


class Images:
    """Pictures stored with their renditions, on the project's objects."""

    def __init__(self, objects: Objects, processor: ImageProcessor) -> None:
        """`objects` where they are stored; `processor` what reads and encodes them."""
        self.objects = objects
        self.processor = processor

    def _kept(
        self,
        written: list[str],
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef:
        """`data` put under `key`, the key remembered in `written` — what is removed if a later
        put fails."""
        ref = self.objects.put(key, data, content_type, metadata)
        written.append(key)
        return ref

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        filename: str,
        sizes: Sequence[ImageSize],
        max_bytes: int = MAX_IMAGE_BYTES,
    ) -> ImagePut:
        """The picture in `data`, sent as `filename`, stored under `key` with its renditions at
        `sizes` — `ImageRefused`, with nothing stored, when it is not taken.

        1. Read, at most `max_bytes`, and processed: refused before anything is written.
        2. The original, as it came, with its name and its size in pixels.
        3. Each rendition beside it (`rendition_key`), with its own size and name.
        Final: what was stored — or, when the store fails midway, nothing: what was written is
        removed and the failure raised.
        """
        read = _read(data, max_bytes)
        processed = self.processor.processed(read, sizes)
        stem = filename.rpartition(".")[0] or filename
        written: list[str] = []
        try:
            original = self._kept(
                written,
                key,
                read,
                processed.content_type,
                {
                    "filename": filename,
                    "width": str(processed.width),
                    "height": str(processed.height),
                },
            )
            renditions = {
                name: self._kept(
                    written,
                    rendition_key(key, name),
                    rendered.data,
                    rendered.content_type,
                    {
                        "filename": f"{stem}@{name}.webp",
                        "width": str(rendered.width),
                        "height": str(rendered.height),
                    },
                )
                for name, rendered in processed.renditions.items()
            }
        except Exception:
            for one in written:
                self.objects.delete(one)
            raise
        return ImagePut(original, renditions)
