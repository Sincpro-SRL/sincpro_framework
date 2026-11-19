"""Images: the sizes a picture is shown at (`ImageSize`), what a processor makes of one (`Processed`),
the port that makes it (`ImageProcessor`) and why a file is refused as a picture (`ImageRefusal`).

    SIZES                                         thumb (320 px a side, a list's line), view (1280 px)
    processor.processed(data, SIZES)              ->  Processed(width=4032, height=3024,
                                                      content_type="image/jpeg", renditions={
                                                        "thumb": Rendered(b"RIFF…", "image/webp", 320, 240),
                                                        "view": Rendered(b"RIFF…", "image/webp", 1280, 960)})
    processor.processed(b"%PDF-1.7", SIZES)       ->  ImageRefused(NOT_AN_IMAGE)

Context: a list of fifty photos loads fifty thumbnails, never fifty originals of 4 MB. The
industry's answer is a few renditions made when the picture is sent — the original kept as it
came, for whoever downloads it — re-encoded: oriented as the camera held it, without what the
camera wrote (its EXIF: the place it was taken, the device), never larger than the original. The
port is what a project replaces: Pillow in-process by default (`PillowImages`), a service of its
own (imgproxy, a cloud's) by another adapter.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple, Protocol

from sincpro_framework.data_layer.object_storage.domain.object_store import ObjectRef


@dataclass(frozen=True)
class ImageSize:
    """A rendition: its name, and the longest side it fits in — the other follows the picture's
    proportion."""

    name: str
    max_side: int


SIZES: tuple[ImageSize, ...] = (ImageSize("thumb", 320), ImageSize("view", 1280))
"""What a picture is shown at by default: a line of a list or a card, and a record or a gallery."""


class Rendered(NamedTuple):
    """One rendition, encoded: its bytes, their type, its size in pixels."""

    data: bytes
    content_type: str
    width: int
    height: int


class Processed(NamedTuple):
    """A picture read: its size in pixels as it is shown (oriented), the type it was sent in — by
    what it is, not by its name —, and its renditions by name."""

    width: int
    height: int
    content_type: str
    renditions: Mapping[str, Rendered]


class ImagePut(NamedTuple):
    """A picture stored: the original as it came, and each rendition by name — each under its
    own key (`rendition_key`), its size in pixels in its metadata (`width`, `height`)."""

    original: ObjectRef
    renditions: Mapping[str, ObjectRef]


def rendition_key(key: str, name: str) -> str:
    """Where the rendition `name` of the picture under `key` is stored: beside it, its name after
    an `@`, WebP.

        rendition_key("photos/01a1e.jpg", "thumb")    ->  "photos/01a1e@thumb.webp"
    """
    folder, _, file = key.rpartition("/")
    stem = file.rpartition(".")[0] or file
    return f"{folder}/{stem}@{name}.webp" if folder else f"{stem}@{name}.webp"


class ImageRefusal(StrEnum):
    """Why a file is not taken as a picture."""

    NOT_AN_IMAGE = "not_an_image"
    """Nothing a picture is read from — a PDF, a text, a broken file."""
    FORMAT_NOT_ALLOWED = "format_not_allowed"
    """A picture in a format this processor does not take."""
    TOO_MANY_PIXELS = "too_many_pixels"
    """More pixels than it decodes — a few kilobytes can claim gigapixels (a decompression bomb)."""
    TOO_LARGE = "too_large"
    """More bytes than a picture is taken with."""


class ImageProcessor(Protocol):
    """Reads a picture and makes its renditions — refusing, with `ImageRefused`, what is not one."""

    def processed(self, data: bytes, sizes: Sequence[ImageSize]) -> Processed:
        """`data` read as a picture, oriented, and encoded at each of `sizes` — none larger
        than the picture, none carrying its metadata."""
        ...
