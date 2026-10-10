"""The counters gapless numbers are taken from."""

from sqlalchemy import BigInteger, Column, MetaData, Table, Text


def numbering_table(name: str, metadata: MetaData) -> Table:
    """The counters `DatabaseNumbering` takes gapless numbers from: one row per series and scope,
    holding the last number taken.

        out     series TEXT · scope TEXT · last BIGINT · PRIMARY KEY (series, scope)

    Declared by the project, in its metadata and its migrations, like every other table.
    """
    return Table(
        name,
        metadata,
        Column("series", Text, primary_key=True),
        Column("scope", Text, primary_key=True),
        Column("last", BigInteger, nullable=False),
    )
