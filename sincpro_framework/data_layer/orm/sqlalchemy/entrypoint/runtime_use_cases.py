"""`SqlUseCases`: the stored use cases of a bounded context in a table of its database — what the
replicas of a service share, so a use case saved by one is loaded by every one on `reload`.

Context: the table is declared on the project's own `MetaData`, next to its other tables, so its
migrations create and change it like any of them. One row per name: saving again updates the row,
and keeps the place it took when it was first saved.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Table,
    func,
    select,
)

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.runtime.runtime_use_cases import RuntimeUseCase, UseCaseStore


class SqlUseCases(UseCaseStore):
    """Stored use cases in `table` on `database` — shared by every replica that reads it."""

    def __init__(self, database: Database, table: Table) -> None:
        """`table` is the one `template_table.use_case_table` declares on the
        project's `MetaData`."""
        self.database = database
        self.table = table

    def active(self) -> list[RuntimeUseCase]:
        """The active use cases, in the order they were first saved (`position`) — a row
        saved again keeps its place.

            store.save(RuntimeUseCase(name="discount", source=..., version=1))
            store.save(RuntimeUseCase(name="tax", source=..., version=1))
            store.save(RuntimeUseCase(name="discount", source=..., version=2))
            [one.name for one in store.active()]      ->  ["discount", "tax"]
            [one.version for one in store.active()]   ->  [2, 1]

        A row saved with `active=False` is kept in the table and left out here.
        """
        table = self.table
        query = select(table).where(table.c.active.is_(True)).order_by(table.c.position)
        with self.database.session() as session:
            rows = session.execute(query).mappings().all()
        return [
            RuntimeUseCase(
                name=row["name"],
                source=row["source"],
                version=row["version"],
                active=row["active"],
                replaces=row["replaces"],
            )
            for row in rows
        ]

    def save(self, use_case: RuntimeUseCase) -> None:
        """Update the row under the use case's name, or add it after the last one.

            store.save(RuntimeUseCase(name="tax", source=...))              ->  position 1
            store.save(RuntimeUseCase(name="fee", source=...))              ->  position 2
            store.save(RuntimeUseCase(name="tax", source=..., version=2))   ->  still 1

        One session: the row looked up by name, then updated — or inserted at the highest
        position plus one — and `saved_at` set to now either way.
        """
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
