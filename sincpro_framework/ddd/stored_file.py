"""`StoredFile`: a file a record holds — the key of its bytes in `ObjectStorage`, and what a person
knows it by: its name as it was sent, its type, its size. An answer writes it with a link to see
it and download it.

    class Organ(Entity):
        photo: StoredFile | None = None
        presentation = Presentation["Organ"](widgets=lambda o: {o.photo: Widget.IMAGE})

    organ.photo = StoredFile.of(storage.put(key, data, metadata={"filename": "directorio.jpg"}))
    Column("photo", TypedJson(StoredFile))                  stored inside the row, as JSON

    an answer, through a gateway that serves files (`FastApiGateway.app(files=storage)`):
        {"key": "photos/01a1….jpg", "name": "directorio.jpg", "content_type": "image/jpeg",
         "size": 48213, "url": "/files/cGhvdG9z….1763600400.Zk3…"}

Context: a bare key — `photos/01a1e….jpg` — lost the name the file was sent with, and gave no
screen a way to tell a picture from a code: the model publishes a `StoredFile` field as a file
(`Widget.FILE`), or as an image when its presentation says so. Its link is the answer's, never
stored: a record answer is written with the link maker of the gateway in play (`linking`), so
whoever may read the record may see its file, and the row keeps the key alone.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Self

from pydantic import ConfigDict, SerializationInfo, model_serializer

from sincpro_framework.data_layer.object_storage.domain.object_store import ObjectRef
from sincpro_framework.sincpro_abstractions import DataTransferObject

LINK = "sincpro_file_link"
"""Where a record answer's serialization context carries the link maker."""

_LINK: ContextVar[Callable[["StoredFile"], str] | None] = ContextVar(
    "sincpro_file_link", default=None
)


class StoredFile(DataTransferObject):
    """A file a record holds. See the module for what it does."""

    model_config = ConfigDict(frozen=True)

    key: str
    """Where its bytes are, in the project's `ObjectStorage`."""
    name: str
    """Its name as it was sent — what a download saves it as."""
    content_type: str
    size: int
    """In bytes."""
    url: str | None = None
    """Only in an answer written by a gateway that serves files: where to see it. Never stored."""

    @classmethod
    def of(cls, ref: ObjectRef) -> Self:
        """The file `put` just stored: its name the `filename` it was put with, else the last
        part of its key."""
        return cls(
            key=ref.key,
            name=ref.metadata.get("filename") or ref.key.rsplit("/", 1)[-1],
            content_type=ref.content_type,
            size=ref.size,
        )

    def __hash__(self) -> int:
        """By what it is — a dict key, as a presentation's `widgets` names it."""
        return hash((self.key, self.name, self.content_type, self.size))

    @model_serializer(mode="wrap")
    def _linked(self, written: Any, info: SerializationInfo) -> Any:
        """With its link in an answer that carries a link maker; without any elsewhere — the
        row it is stored in keeps no link that would end."""
        out = written(self)
        out.pop("url", None)
        link = (info.context or {}).get(LINK)
        if link is not None:
            out["url"] = link(self)
        return out


@contextmanager
def linking(link: Callable[[StoredFile], str]) -> Generator[None]:
    """The block in which every record answer writes each `StoredFile` with `link(file)` — what
    a gateway that serves files puts around each request.

        with linking(lambda file: f"/files/{storage.link(file.key)}"):
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
