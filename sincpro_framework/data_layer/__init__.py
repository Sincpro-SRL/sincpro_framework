"""Where the data is stored: the repositories that need no database (`repositories`), SQL (`orm`),
the cache and its engines (`caching`), how a schema changes (`migrations`) and DataFrames
(`data_analysis`).

    from sincpro_framework.data_layer.repositories import MemoryRepository   # tests
    from sincpro_framework.data_layer.orm import Database, Repository        # [sqlalchemy]
    from sincpro_framework.data_layer.caching import Cache, QueryCaching      # cached answers
    from sincpro_framework.data_layer.migrations import Migrations, command_line
    from sincpro_framework.data_layer.data_analysis import QueryCache        # as frames

    bus.add_dependency("repository", MemoryRepository(Account(code="1010", …)))   # in a test

Context: each subpackage is imported on its own, so a service pays only for what it uses —
`orm` needs the `[sqlalchemy]` extra, Parquet and Arrow the `[data-analysis]` one; this
package imports nothing.
"""
