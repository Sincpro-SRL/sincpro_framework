"""The relation vocabulary with no database: what a node's page means, and what a resolver
answers."""

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity.relations import (
    NESTED_LIMIT,
    BusResolver,
    Relation,
    Resolver,
    limit_of,
)


def test_a_node_that_said_nothing_gets_the_default_and_one_that_said_the_default_keeps_it():
    """`limit=50` written by the client is 50, although 50 is also the default: what was said
    is read off the fields that were set, not off the value."""
    assert limit_of(Criteria(), whole=False) == NESTED_LIMIT
    assert limit_of(Criteria.model_validate({"pagination": {"limit": 50}}), whole=False) == 50
    assert limit_of(Criteria.model_validate({"pagination": {"limit": 3}}), whole=False) == 3
    assert limit_of(Criteria.model_validate({"pagination": {"limit": 3}}), whole=True) is None


def test_a_bus_resolver_is_a_resolver_and_carries_the_criteria_in_the_command():
    seen = {}

    class CommandSearch:
        def __init__(self, criteria: Criteria) -> None:
            seen["criteria"] = criteria

    def bus(command):
        seen["command"] = command
        return []

    relation = Relation.bus(object, bus, CommandSearch, identified_by="parent_id")

    assert isinstance(relation.resolver, Resolver) and isinstance(
        relation.resolver, BusResolver
    )
    assert relation.resolver(["a"], Criteria()) == []
    assert isinstance(seen["command"], CommandSearch) and seen["criteria"] == Criteria()
