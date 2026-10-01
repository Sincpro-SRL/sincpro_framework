"""A query is read strictly: a key the language does not have is refused, never ignored.

    Criteria.model_validate({"limit": 10})                      →  refused: 'limit' is not a key here
    Criteria.model_validate({"limit": 10}, context=TOLERANT)    →  read, 'limit' left out

Context: a typo in a query does not fail, it answers wrong — a dropped filter brings back more
rows than were asked for. RFC 9413 calls the cure virtuous intolerance; GraphQL, JSON:API query
parameters and Elasticsearch's query DSL all refuse what they do not know. `TOLERANT` is for the
client of another version, asked for where it is read.
"""

from typing import Any

from pydantic import ConfigDict, ValidationInfo, model_validator

from sincpro_framework.sincpro_abstractions import DataTransferObject

TOLERANT: dict[str, Any] = {"unknown_keys": "ignore"}
"""The validation context that reads a query leaving out the keys it does not know."""


def tolerates(info: ValidationInfo | None) -> bool:
    return bool(
        info is not None and info.context and info.context.get("unknown_keys") == "ignore"
    )


class Strict(DataTransferObject):
    """A part of the query language: an unknown key is refused, unless the reading tolerates it."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _without_unknown_keys_when_tolerated(cls, data: Any, info: ValidationInfo) -> Any:
        if not (isinstance(data, dict) and tolerates(info)):
            return data
        known = set(cls.model_fields) | {
            f.alias for f in cls.model_fields.values() if f.alias
        }
        return {key: value for key, value in data.items() if key in known}
