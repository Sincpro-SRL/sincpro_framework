"""`StoredFile`: a file a record holds — the key of its bytes in `ObjectStorage`, and what a person
knows it by: its name as it was sent, its type, its size; a picture also its size in pixels and its
renditions (`Rendition`), the smaller copies a screen shows. An answer writes each with a link.

    class Organ(Entity):
        photo: StoredFile | None = None
        statute: StoredFile | None = None
        presentation = Presentation["Organ"](widgets=lambda o: {o.photo: Widget.IMAGE})

    organ.photo = StoredFile.of(*storage.put_image(key, data, "Directorio.jpg"))   with its renditions
    organ.statute = StoredFile.of(storage.put(key, data, metadata={"filename": "Estatuto.pdf"}))
    Column("photo", TypedJson(StoredFile))                  stored inside the row, as JSON

    an answer, through a gateway that serves files (`FastApiGateway.app(files=storage)`):
        {"key": "photos/01a1e.jpg", "name": "Directorio.jpg", "content_type": "image/jpeg",
         "size": 2481203, "width": 4032, "height": 3024, "url": "/files/cGhvdG9z….Zk3",
         "renditions": [{"name": "thumb", "key": "photos/01a1e@thumb.webp", "width": 320, …,
                         "url": "/files/cGhvdG9zL….Ab1"}, {"name": "view", …}]}

Context: a bare key — `photos/01a1e….jpg` — lost the name the file was sent with, and gave no
screen a way to tell a picture from a code: the model publishes a `StoredFile` field as a file
(`Widget.FILE`), or as an image when its presentation says so; a list shows its `thumb`, a record
its `view`, a download the original. Its links are the answer's, never stored: a record answer is
written with the link maker of the gateway in play (`linking`), so whoever may read the record may
see its file, and the row keeps the keys alone.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Self

from pydantic import ConfigDict, SerializationInfo, model_serializer

from sincpro_framework.data_layer.object_storage.domain.object_store import ObjectRef
from sincpro_framework.sincpro_abstractions import DataTransferObject

LINK = "sincpro_file_link"
"""Where a record answer's serialization context carries the link maker."""

_LINK: ContextVar[Callable[[str], str] | None] = ContextVar("sincpro_file_link", default=None)


def _pixels(ref: ObjectRef, side: str) -> int | None:
    """A side in pixels, as `put_image` wrote it in the object's metadata."""
    said = ref.metadata.get(side, "")
    return int(said) if said.isdigit() else None


class _Stored(DataTransferObject):
    """What is stored and shown by a link: its key, its type, its size, and its `url` only in an
    answer."""

    model_config = ConfigDict(frozen=True)

    key: str
    """Where its bytes are, in the project's `ObjectStorage`."""
    content_type: str
    size: int
    """In bytes."""
    url: str | None = None
    """Only in an answer written by a gateway that serves files: where to see it. Never stored."""

    @model_serializer(mode="wrap")
    def _linked(self, written: Any, info: SerializationInfo) -> Any:
        """With its link in an answer that carries a link maker; without any elsewhere — the
        row it is stored in keeps no link that would end."""
        out = written(self)
        out.pop("url", None)
        link = (info.context or {}).get(LINK)
        if link is not None:
            out["url"] = link(self.key)
        return out


class Rendition(_Stored):
    """A smaller copy of a stored picture: `thumb` for a list's line, `view` for a record."""

    name: str
    """Which one — the `ImageSize` it was made at."""
    width: int
    height: int

    @classmethod
    def of(cls, name: str, ref: ObjectRef) -> Self:
        """The rendition `put_image` stored as `name`."""
        return cls(
            name=name,
            key=ref.key,
            content_type=ref.content_type,
            size=ref.size,
            width=_pixels(ref, "width") or 0,
            height=_pixels(ref, "height") or 0,
        )


class StoredFile(_Stored):
    """A file a record holds. See the module for what it does."""

    name: str
    """Its name as it was sent — what a download saves it as."""
    width: int | None = None
    """A picture's, in pixels, as it is shown."""
    height: int | None = None
    renditions: list[Rendition] = []
    """A picture's smaller copies; none for any other file."""

    @classmethod
    def of(cls, ref: ObjectRef, renditions: Mapping[str, ObjectRef] | None = None) -> Self:
        """The file `put` just stored — or the picture `put_image` did, with its renditions
        (`StoredFile.of(*put)`): its name the `filename` it was put with, else the last part of
        its key."""
        return cls(
            key=ref.key,
            name=ref.metadata.get("filename") or ref.key.rsplit("/", 1)[-1],
            content_type=ref.content_type,
            size=ref.size,
            width=_pixels(ref, "width"),
            height=_pixels(ref, "height"),
            renditions=[Rendition.of(name, one) for name, one in (renditions or {}).items()],
        )

    @property
    def keys(self) -> list[str]:
        """Every key it is stored under — the original's and its renditions' —, what removing
        it removes: `for key in photo.keys: storage.delete(key)`."""
        return [self.key, *(one.key for one in self.renditions)]

    def __hash__(self) -> int:
        """By what it is — a dict key, as a presentation's `widgets` names it."""
        return hash((self.name, self.size, *self.keys))

    def rendition(self, name: str) -> Rendition | None:
        """The rendition `name`, when it has one."""
        return next((one for one in self.renditions if one.name == name), None)


@contextmanager
def linking(link: Callable[[str], str]) -> Generator[None]:
    """The block in which every record answer writes each stored file and rendition with
    `link(key)` — what a gateway that serves files puts around each request.

        with linking(lambda key: f"/files/{storage.link(key)}"):
            ResponseRecord(...).model_dump(mode="json")      {"photo": {…, "url": "/files/…"}}
    """
    token = _LINK.set(link)
    try:
        yield
    finally:
        _LINK.reset(token)


def answer_context() -> dict[str, Any] | None:
    """The serialization context a record answer is written with: the link maker in play, if
    any."""
    link = _LINK.get()
    return None if link is None else {LINK: link}
