"""A picture stored with its renditions (`ObjectStorage.put_image`, `PillowImages`): the original as it
came, each size re-encoded beside it — oriented, without the camera's metadata, never larger, its
transparency kept —, and what is not a picture refused with nothing stored."""

import io
from typing import Any

import pytest
from PIL import Image

from sincpro_framework.data_layer.object_storage import (
    ImageRefusal,
    ImageRefused,
    ImageSize,
    InMemoryObjectStore,
    ObjectStorage,
    PillowImages,
    rendition_key,
)
from sincpro_framework.exceptions import ProgrammingError

ORIENTATION = 0x0112
GPS = 0x8825


def picture(
    width: int, height: int, format: str = "JPEG", mode: str = "RGB", **save: Any
) -> bytes:
    written = io.BytesIO()
    Image.new(mode, (width, height), "navy").save(written, format, **save)
    return written.getvalue()


def storage(store: Any = None, **images: Any) -> ObjectStorage:
    return ObjectStorage(
        store or InMemoryObjectStore(), "secret", images=PillowImages(**images)
    )


def opened(files: ObjectStorage, key: str) -> Any:
    return Image.open(io.BytesIO(files.get(key)))


def test_the_original_is_kept_as_it_came_and_each_size_is_written_beside_it_as_webp():
    files = storage()
    sent = picture(4000, 3000)

    put = files.put_image("photos/01a1e.jpg", sent, "Directorio 2026.jpg")

    assert files.get("photos/01a1e.jpg") == sent
    assert put.original.metadata == {
        "filename": "Directorio 2026.jpg",
        "width": "4000",
        "height": "3000",
    }
    thumb, view = put.renditions["thumb"], put.renditions["view"]
    assert (thumb.key, thumb.content_type) == ("photos/01a1e@thumb.webp", "image/webp")
    assert opened(files, thumb.key).size == (320, 240)
    assert opened(files, view.key).size == (1280, 960)
    assert view.metadata == {
        "filename": "Directorio 2026@view.webp",
        "width": "1280",
        "height": "960",
    }


def test_a_rendition_is_never_larger_than_the_picture():
    put = storage().put_image("icons/logo.png", picture(200, 100, "PNG"), "logo.png")

    assert put.renditions["view"].metadata["width"] == "200"
    assert put.renditions["thumb"].metadata["height"] == "100"


def test_it_is_turned_as_the_camera_held_it_and_keeps_nothing_the_camera_wrote():
    exif = Image.Exif()
    exif[ORIENTATION] = 6  # the phone was held upright: the pixels are on their side
    exif[GPS] = {1: "S", 2: (16.0, 30.0, 0.0)}
    files = storage()

    put = files.put_image("photos/portrait.jpg", picture(400, 200, exif=exif), "retrato.jpg")
    thumb = opened(files, put.renditions["thumb"].key)

    assert (put.original.metadata["width"], put.original.metadata["height"]) == ("200", "400")
    assert thumb.size == (160, 320)
    assert dict(thumb.getexif()) == {}
    assert "exif" not in thumb.info


def test_a_transparent_picture_keeps_its_transparency():
    seal = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # clear, a navy square in its middle
    seal.paste((0, 0, 128, 255), (16, 16, 48, 48))
    sent = io.BytesIO()
    seal.save(sent, "PNG")
    files = storage()

    put = files.put_image("logos/sello.png", sent.getvalue(), "sello.png")
    thumb = opened(files, put.renditions["thumb"].key)

    assert thumb.mode == "RGBA"
    assert thumb.getpixel((0, 0))[3] == 0


def test_its_type_is_what_it_is_not_what_its_name_says():
    put = storage().put_image("photos/01a1e.jpg", picture(10, 10, "PNG"), "captura.jpg")

    assert put.original.content_type == "image/png"


@pytest.mark.parametrize(
    ("sent", "images", "reason"),
    [
        (b"%PDF-1.7 a statute", {}, ImageRefusal.NOT_AN_IMAGE),
        (picture(40, 40)[:200], {}, ImageRefusal.NOT_AN_IMAGE),  # a broken body
        (picture(40, 40, "BMP"), {}, ImageRefusal.FORMAT_NOT_ALLOWED),
        (picture(100, 100), {"max_pixels": 1000}, ImageRefusal.TOO_MANY_PIXELS),
    ],
    ids=["a pdf", "a broken jpeg", "a bmp", "too many pixels"],
)
def test_what_is_not_taken_as_a_picture_is_refused_and_nothing_is_stored(
    sent, images, reason
):
    files = storage(**images)

    with pytest.raises(ImageRefused) as refused:
        files.put_image("photos/x.jpg", sent, "x.jpg")

    assert refused.value.reason is reason
    assert list(files.keys()) == []


def test_more_bytes_than_a_picture_is_taken_with_are_refused_before_reading_it():
    files = storage()

    with pytest.raises(ImageRefused) as refused:
        files.put_image("photos/x.jpg", io.BytesIO(picture(400, 300)), "x.jpg", max_bytes=100)

    assert refused.value.reason is ImageRefusal.TOO_LARGE


class FailsOnView(InMemoryObjectStore):
    """A store that fails when the last rendition is written."""

    def put(self, key: str, *args: Any, **kwargs: Any) -> Any:
        if "@view" in key:
            raise ConnectionError("the bucket went away")
        return super().put(key, *args, **kwargs)


def test_a_store_that_fails_midway_keeps_none_of_it():
    files = storage(FailsOnView())

    with pytest.raises(ConnectionError):
        files.put_image("photos/01a1e.jpg", picture(400, 300), "Directorio.jpg")

    assert list(files.keys()) == []


def test_the_sizes_are_the_callers():
    put = storage().put_image(
        "photos/01a1e.jpg", picture(2000, 1000), "x.jpg", sizes=(ImageSize("card", 600),)
    )

    assert list(put.renditions) == ["card"]
    assert put.renditions["card"].metadata["width"] == "600"


def test_without_a_processor_there_is_no_picture_to_put():
    files = ObjectStorage(InMemoryObjectStore(), "secret")

    with pytest.raises(ProgrammingError, match="PillowImages"):
        files.put_image("photos/x.jpg", picture(10, 10), "x.jpg")


@pytest.mark.parametrize(
    ("key", "rendition"),
    [
        ("photos/01a1e.jpg", "photos/01a1e@thumb.webp"),
        ("a/b/c.tar.png", "a/b/c.tar@thumb.webp"),
        ("noextension", "noextension@thumb.webp"),
    ],
)
def test_a_rendition_lives_beside_its_picture(key, rendition):
    assert rendition_key(key, "thumb") == rendition
