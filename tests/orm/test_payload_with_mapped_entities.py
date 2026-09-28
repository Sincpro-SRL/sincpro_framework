"""A mapped aggregate carried to or from another service comes back as a real instance of its class.

pydantic fills a dataclass's `__dict__` without calling its `__init__`, so an aggregate SQLAlchemy
instruments came back with no instance state — every attribute failed as soon as the mapper was in
use. The payload builds each dataclass through its own constructor, as the class would be built here.
"""

from pydantic import BaseModel

from sincpro_framework.remote_execution import pack, unpack

from .models import Thing, a_thing


class ResponseWithThing(BaseModel):
    thing: Thing
    things: list[Thing]


def test_a_mapped_aggregate_comes_back_with_its_instance_state(database):
    rebuilt = unpack(pack(a_thing(1)), Thing)

    assert "_sa_instance_state" in vars(rebuilt)
    assert rebuilt.name == a_thing(1).name


def test_a_mapped_aggregate_inside_a_response_comes_back_whole(database):
    response = ResponseWithThing(thing=a_thing(1), things=[a_thing(2), a_thing(3)])

    rebuilt = unpack(pack(response), ResponseWithThing)

    assert rebuilt.thing.name == a_thing(1).name
    assert [one.name for one in rebuilt.things] == [a_thing(2).name, a_thing(3).name]
    assert all("_sa_instance_state" in vars(one) for one in (rebuilt.thing, *rebuilt.things))
