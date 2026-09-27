"""`SqlUseCases`: the stored use cases of a bounded context in a table of its database — what the
replicas of a service share, so a use case saved by one is loaded by every one on `reload`.

Context: the table is declared on the project's own `MetaData`, next to its other tables, so its
migrations create and change it like any of them. One row per name: saving again updates the row,
and keeps the place it took when it was first saved.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
)

from sincpro_framework.orm.sqlalchemy import Database
from sincpro_framework.runtime_use_cases import RuntimeUseCase, UseCaseStore


def use_case_table(metadata: MetaData, name: str = "runtime_use_case") -> Table:
    """The table of a context's stored use cases, on `metadata` — named for the context when
    several share a database: `use_case_table(metadata, "billing_use_case")`."""
    return Table(
        name,
        metadata,
        Column("name", String(200), primary_key=True),
        Column("position", Integer, nullable=False),
        Column("source", Text, nullable=False),
        Column("version", Integer, nullable=False),
        Column("active", Boolean, nullable=False),
        Column("replaces", String(400), nullable=True),
        Column("saved_at", DateTime(timezone=True), nullable=False),
    )


class SqlUseCases(UseCaseStore):
    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def active(self) -> list[RuntimeUseCase]:
        table = self.table
        query = select(table).where(table.c.active.is_(True)).order_by(table.c.position)
        with self.database.session() as session:
            rows = session.execute(query).mappings().all()
        return [
            RuntimeUseCase(
                row["name"], row["source"], row["version"], row["active"], row["replaces"]
            )
            for row in rows
        ]

    def save(self, use_case: RuntimeUseCase) -> None:
        """Update the row under the use case's name, or add it after the last one."""
        table = self.table
        values = {
            "source": use_case.source,
            "version": use_case.version,
            "active": use_case.active,
            "replaces": use_case.replaces,
            "saved_at": datetime.now(UTC),
        }
        with self.database.session() as session:
            saved = session.scalar(select(table.c.name).where(table.c.name == use_case.name))
            if saved is not None:
                session.execute(
                    table.update().where(table.c.name == use_case.name).values(values)
                )
                return
            last = session.execute(
                select(func.coalesce(func.max(table.c.position), 0))
            ).scalar_one()
            session.execute(
                table.insert().values(name=use_case.name, position=last + 1, **values)
            )
