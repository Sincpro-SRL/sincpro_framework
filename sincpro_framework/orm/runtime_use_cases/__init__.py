"""The SQL store for `sincpro_framework.runtime_use_cases` — behind the `[sqlalchemy]` extra.

from sincpro_framework.orm.runtime_use_cases import SqlUseCases, use_case_table

table = use_case_table(tables.metadata)              # migrated with the context's tables
registry = BusRegistry(billing, SqlUseCases(database, table))
"""

from sincpro_framework.orm.runtime_use_cases.store import SqlUseCases, use_case_table

__all__ = ["SqlUseCases", "use_case_table"]
