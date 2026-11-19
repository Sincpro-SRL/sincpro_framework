"""A gateway that serves files (`FastApiGateway.app(files=storage)`): a record answer carries the
link of each file it holds, and `GET /files/{token}` gives the bytes back with the name they were
sent with — inline to see them, as an attachment to save them —, a link that ended or was not
made here refused."""

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.object_storage import (
    InMemoryObjectStore,
    ObjectStorage,
    PillowImages,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, Get, ResponseRecord, StoredFile
from sincpro_framework.entrypoints.entrypoint.decorators import rest

JPEG = b"\xff\xd8\xff\xe0 a picture"


@dataclass(kw_only=True)
class Organ(Entity):
    name: str = ""
    photo: StoredFile | None = None


class ResponseOrgan(ResponseRecord):
    organ: Organ


class QueryGetOrgan(Get[Organ, ResponseOrgan]):
    pass


class Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.at


def site(clock: Clock, picture: bool = False) -> tuple[UseFramework, ObjectStorage, Organ]:
    """A site with one organ: its photo put as it came — or, as a `picture`, with renditions."""
    bus = UseFramework("site", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    storage = ObjectStorage(
        InMemoryObjectStore(), "the-service-secret", now=clock, images=PillowImages()
    )
    if picture:
        sent = io.BytesIO()
        Image.new("RGB", (1600, 900), "navy").save(sent, "JPEG")
        photo = StoredFile.of(
            *storage.put_image("photos/01a1f.jpg", sent.getvalue(), "T.jpg")
        )
    else:
        photo = StoredFile.of(
            storage.put("photos/01a1e.jpg", JPEG, metadata={"filename": "Directorio ñ.jpg"})
        )
    organ = Organ(name="Directorio", photo=photo)
    repository.save(organ)

    @bus.feature(QueryGetOrgan)
    @rest.get("/organs/{id}")
    class OrganReads(EntityReads[Organ]):
        pass

    return bus, storage, organ


def client(clock: Clock, files_path: str = "/files", picture: bool = False):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    bus, storage, organ = site(clock, picture)
    gateway = FastApiGateway([bus], unguarded=True)
    return TestClient(gateway.app(files=storage, files_path=files_path)), organ


def test_a_record_answer_carries_the_link_of_its_file_and_the_link_gives_it_back():
    http, organ = client(Clock())

    photo = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]
    served = http.get(photo["url"])

    assert photo["name"] == "Directorio ñ.jpg"
    assert photo["url"].startswith("/files/")
    assert served.status_code == 200, served.text
    assert served.content == JPEG
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["content-disposition"] == (
        "inline; filename=\"Directorio n.jpg\"; filename*=UTF-8''Directorio%20%C3%B1.jpg"
    )
    assert served.headers["cache-control"].startswith("private, max-age=")


def test_a_picture_answers_its_renditions_each_served_by_its_own_link():
    http, organ = client(Clock(), picture=True)

    photo = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]
    thumb = next(one for one in photo["renditions"] if one["name"] == "thumb")
    served = http.get(thumb["url"])

    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(served.content)).size == (320, 180)


def test_asked_with_download_it_is_saved_under_its_name():
    http, organ = client(Clock())
    url = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]["url"]

    served = http.get(url, params={"download": ""})

    assert served.headers["content-disposition"].startswith("attachment;")


def test_a_link_that_ended_is_gone_and_one_not_made_here_is_not_found():
    clock = Clock()
    http, organ = client(clock)
    url = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]["url"]

    clock.at += timedelta(hours=3)

    assert http.get(url).status_code == 410
    assert http.get("/files/cGhvdG9z.1.forged").status_code == 404


def test_served_elsewhere_the_route_and_the_links_follow_its_path():
    http, organ = client(Clock(), files_path="/v1/files")

    url = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]["url"]

    assert url.startswith("/v1/files/")
    assert http.get(url).content == JPEG


def test_without_files_a_record_answer_carries_no_link():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    bus, _, organ = site(Clock())
    http = TestClient(FastApiGateway([bus], unguarded=True).app())

    photo = http.get(f"/site/organs/{organ.id}").json()["organ"]["photo"]

    assert photo["name"] == "Directorio ñ.jpg"
    assert "url" not in photo
