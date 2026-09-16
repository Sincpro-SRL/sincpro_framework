"""The SQLAlchemy backend. One module per responsibility, named after what it does:

    database.py             `Database`: one engine, one session factory, observed from birth
    repository.py           `Repository`: runs a `Criteria`, keeps what a use case built
    sql_translator.py       `Criteria` → `Select`; the grain registry per dialect
    data_mapper.py          the Data Mapper: tables and the mapping call, `Entity` columns
    model_introspection.py  `describe(cls) → Meta`: asks the mapper what a class looks like
    custom_fields.py        column types: `JsonText`, `TranslatedText`
    observability.py        every statement to the logger, the tracer and the error tracker

Everything here knows SQLAlchemy; nothing in `sincpro_framework.ddd` does. Rewriting this
package is what a different backend would cost.
"""

from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText, TranslatedText
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    entity_columns,
    entity_table,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository
from sincpro_framework.orm.sqlalchemy.sql_translator import register_grain_translator

__all__ = [
    "Database",
    "JsonText",
    "Repository",
    "TranslatedText",
    "describe",
    "entity_columns",
    "entity_table",
    "map_aggregates",
    "register_grain_translator",
]
