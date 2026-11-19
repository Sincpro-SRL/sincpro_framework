"""A record that holds a file (`StoredFile`): published as a file — an image when its presentation
says so —, written in a record answer with its link while a link maker is in play (`linking`), and
stored without one."""

from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select

from sincpro_framework.data_layer.object_storage import InMemoryObjectStore, ObjectStorage
from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import TypedJson
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Entity,
    Presentation,
    ResponseRecord,
    StoredFile,
    Widget,
    linking,
)


@dataclass(kw_only=True)
class Organ(Entity):
    name: str = ""
    photo: StoredFile | None = None
    statute: StoredFile | None = None

    presentation = Presentation["Organ"](widgets=lambda o: {o.photo: Widget.IMAGE})


class ResponseOrgan(ResponseRecord):
    organ: Organ


def stored() -> StoredFile:
    files = ObjectStorage(InMemoryObjectStore(), "the-service-secret")
    return StoredFile.of(
        files.put(
            "photos/01a1e.jpg", b"\xff\xd8", metadata={"filename": "Directorio 2026.jpg"}
        )
    )


def test_put_with_its_filename_it_keeps_the_name_it_was_sent_with():
    photo = stored()

    assert (photo.key, photo.name, photo.content_type, photo.size) == (
        "photos/01a1e.jpg",
        "Directorio 2026.jpg",
        "image/jpeg",
        2,
    )


def test_put_without_a_filename_it_is_named_by_the_end_of_its_key():
    files = ObjectStorage(InMemoryObjectStore())

    assert (
        StoredFile.of(files.put("statutes/2026/estatuto.pdf", b"%PDF")).name == "estatuto.pdf"
    )


def test_it_is_published_as_a_file_or_as_an_image_never_sorted_nor_grouped():
    fields = MemoryRepository().definition(Organ).fields

    assert fields["statute"].widget == "file"
    assert fields["photo"].widget == "image"
    assert not fields["photo"].sortable and not fields["photo"].groupable


def test_a_record_answer_writes_it_with_its_link_while_a_link_maker_is_in_play():
    answer = ResponseOrgan(organ=Organ(name="Directorio", photo=stored()))

    with linking(lambda file: f"/files/{file.key}"):
        written = answer.model_dump(mode="json")["organ"]

    assert written["photo"] == {
        "key": "photos/01a1e.jpg",
        "name": "Directorio 2026.jpg",
        "content_type": "image/jpeg",
        "size": 2,
        "url": "/files/photos/01a1e.jpg",
    }
    assert written["statute"] is None


def test_outside_a_gateway_that_serves_files_it_is_written_without_a_link():
    answer = ResponseOrgan(organ=Organ(name="Directorio", photo=stored()))

    assert "url" not in answer.model_dump(mode="json")["organ"]["photo"]


def test_stored_in_its_row_it_keeps_no_link_even_while_one_is_made():
    metadata = MetaData()
    organs = Table(
        "organ",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("photo", TypedJson(StoredFile)),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    with linking(lambda file: f"/files/{file.key}"), engine.begin() as connection:
        connection.execute(insert(organs), [{"id": 1, "photo": stored()}])
        raw = connection.exec_driver_sql("SELECT photo FROM organ").scalar_one()
        read = connection.execute(select(organs.c.photo)).scalar_one()

    assert "url" not in raw
    assert read == stored()
