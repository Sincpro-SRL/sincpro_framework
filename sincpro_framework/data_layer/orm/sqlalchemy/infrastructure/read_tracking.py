"""Every statement a session runs notes the aggregates of the tables it reads.

Context: listened to on the session factory, so it sees what a repository method, a relation
the resolver loads, a count or a hand-written `session.execute` reads — by the tables of the
statement, not by which method ran it. It costs a context-variable read while nobody is noting.
"""

from sqlalchemy import Table
from sqlalchemy.orm import ORMExecuteState
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.util import find_tables

from sincpro_framework.data_layer.orm.sqlalchemy.domain.registry import aggregates_of
from sincpro_framework.ddd.repositories.reads import note_read, reading


def note_statement_reads(state: ORMExecuteState) -> None:
    statement = state.statement
    if not reading() or not state.is_select or not isinstance(statement, ClauseElement):
        return
    for table in find_tables(statement, include_joins=True):
        if isinstance(table, Table):
            for aggregate in aggregates_of(table):
                note_read(aggregate)
