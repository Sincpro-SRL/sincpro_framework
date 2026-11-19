"""`PillowImages`: the `ImageProcessor` that reads and encodes pictures in-process with Pillow — the
`images` extra (`pip install sincpro-framework[images]`).

    storage = ObjectStorage(store, secret, images=PillowImages())
    PillowImages(max_pixels=60_000_000, quality=85)        a larger picture, a finer encoding
    PillowImages(formats=frozenset({"JPEG", "PNG"}))       only what a camera and a screenshot give

Context: what the industry does with a picture it is sent, in the order it matters:
1. it refuses what it will not decode — not a picture, a format not taken, more pixels than it
   decodes (`max_pixels`: a few kilobytes can claim gigapixels and take the process's memory);
2. it turns it as the camera held it (EXIF orientation) — a phone's portrait stays a portrait;
3. it writes each rendition anew, WebP, at most `max_side` a side and never larger than the
   picture, its transparency kept, its colour profile kept (a phone's Display P3) and nothing
   else of what the camera wrote — the place it was taken, the device, the time.
Animated pictures are rendered from their first frame. The original is not touched here: whoever
stores it keeps it as it came.
"""

import io
from collections.abc import Sequence
from typing import Any

from sincpro_framework.data_layer.object_storage.domain.exceptions import ImageRefused
from sincpro_framework.data_layer.object_storage.domain.images import (
    ImageRefusal,
    ImageSize,
    Processed,
    Rendered,
)

FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})
"""What is taken by default: what a camera, a screenshot and the web give."""

MAX_PIXELS = 40_000_000
"""Forty megapixels: past any phone, short of what would take a worker's memory."""


class PillowImages:
    """Pictures read and encoded with Pillow, in-process."""

    def __init__(
        self,
        max_pixels: int = MAX_PIXELS,
        quality: int = 80,
        formats: frozenset[str] = FORMATS,
    ) -> None:
        """`max_pixels` the most it decodes; `quality` the WebP quality of each rendition, 0 to
        100; `formats` what it takes, as Pillow names them (`JPEG`, `PNG`, `WEBP`, `GIF`…)."""
        # Imported here: the `images` extra is needed by whoever processes pictures, no one else.
        from PIL import Image, ImageOps

        self.image: Any = Image
        self.ops: Any = ImageOps
        self.max_pixels = max_pixels
        self.quality = quality
        self.formats = formats

    def _opened(self, data: bytes) -> tuple[Any, str]:
        """The picture in `data`, decoded and turned as the camera held it, and the type it was
        sent in — refused when it is not one, not taken, or has more pixels than this decodes,
        before decoding them.

        1. Its header read: nothing a picture is read from is `NOT_AN_IMAGE`.
        2. Its format and its size, from the header alone — refused before a pixel is decoded.
        3. Decoded: a broken body is `NOT_AN_IMAGE` too.
        Final: oriented by its EXIF, the orientation tag gone with it; its type, by its format.
        """
        try:
            picture = self.image.open(io.BytesIO(data))
        except self.image.DecompressionBombError as bomb:
            raise ImageRefused(ImageRefusal.TOO_MANY_PIXELS, str(bomb)) from bomb
        except (self.image.UnidentifiedImageError, OSError) as unread:
            raise ImageRefused(
                ImageRefusal.NOT_AN_IMAGE, "the file is not a picture"
            ) from unread
        if picture.format not in self.formats:
            raise ImageRefused(
                ImageRefusal.FORMAT_NOT_ALLOWED,
                f"{picture.format} is not taken: {', '.join(sorted(self.formats))}",
            )
        if picture.width * picture.height > self.max_pixels:
            raise ImageRefused(
                ImageRefusal.TOO_MANY_PIXELS,
                f"{picture.width}×{picture.height} is more than {self.max_pixels} pixels",
            )
        try:
            picture.load()
        except OSError as broken:
            raise ImageRefused(ImageRefusal.NOT_AN_IMAGE, "the picture is broken") from broken
        sent_as = self.image.MIME.get(picture.format, "application/octet-stream")
        return self.ops.exif_transpose(picture), sent_as

    def _rendered(self, picture: Any, size: ImageSize) -> Rendered:
        """`picture` at most `size.max_side` a side, never larger, as WebP — its transparency
        and colour profile kept, nothing else of its metadata."""
        clear = picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info
        rendition = picture.convert("RGBA" if clear else "RGB")
        rendition.thumbnail((size.max_side, size.max_side), self.image.Resampling.LANCZOS)
        written = io.BytesIO()
        profile = picture.info.get("icc_profile")
        rendition.save(
            written,
            format="WEBP",
            quality=self.quality,
            method=4,
            **({} if profile is None else {"icc_profile": profile}),
        )
        return Rendered(written.getvalue(), "image/webp", rendition.width, rendition.height)

    def processed(self, data: bytes, sizes: Sequence[ImageSize]) -> Processed:
        """`data` read as a picture and encoded at each of `sizes` — see the module."""
        picture, sent_as = self._opened(data)
        return Processed(
            width=picture.width,
            height=picture.height,
            content_type=sent_as,
            renditions={size.name: self._rendered(picture, size) for size in sizes},
        )
