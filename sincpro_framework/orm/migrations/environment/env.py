"""The Alembic environment every `AlembicEngine` chain runs in — no `alembic.ini`.

The engine hands over, in `config.attributes`, the connection, the context's `MetaData` and the
chain's version table.
"""

from alembic import context

from sincpro_framework.orm.migrations.engine import only_tables_of

config = context.config
connection = config.attributes["connection"]
metadata = config.attributes["metadata"]

context.configure(
    connection=connection,
    target_metadata=metadata,
    version_table=config.attributes["version_table"],
    include_name=only_tables_of(metadata),
    render_as_batch=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
