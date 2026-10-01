"""Runs a `Criteria` against a database and answers an `EntityCollection`; persists what a use
case built or changed.

The single door for reads and the single door for writes: `Repository` is the unit of work and
the typed surface, made of `Reading` and `Writing` (`services/`), so a concern added later — an
access mask, a cache, an audit — lands behind this one class. Reads are generic because a filter is a filter;
writes take the aggregate, whole with the children it owns, because a write that skips the
aggregate skips its rules. The two that skip it on purpose — `upsert`, and `update_all` /
`remove_all` by criteria — are named for it.

    page = self.repository.search(Dataset, criteria)      a page, with cursor, count and definition
    one = self.repository.get(Dataset, "ds_01a0…")        by identity, or None
    self.repository.save(dataset)                         insert or update, version checked
    with self.repository.context(isolation=Isolation.SERIALIZABLE) as repository:
        run = repository.get(Run, run_id)                 several of those, one transaction
        run.advance()
        repository.save(run)
        repository.after_commit(lambda: announce(run))
"""

from sincpro_framework.orm.sqlalchemy.services.workflows.reading import Reading
from sincpro_framework.orm.sqlalchemy.services.workflows.unit_of_work import UnitOfWork
from sincpro_framework.orm.sqlalchemy.services.workflows.writing import Writing


class Repository(UnitOfWork, Reading, Writing):
    """One database, read and written through one object. Implements `ddd.Repository` and
    answers more: a use case takes this instance, the protocol holds it to the minimum.

    Built once per bounded context and injected as `self.repository`. Every call opens its own
    session and commits it — except inside `context`, where the engine handed to the
    block shares one session and the block is the transaction.
    """
