"""The machinery behind the repository, in two kinds, as Features and ApplicationServices are:

    atomic       one job each — the data mapper (the mapping API a project writes its
                 tables with, exported by `sincpro_framework.data_layer.orm`), the SQL
                 translator, the relation resolver, the cascade, the upsert, `ModelIntrospection.describe`
    workflows/   what orchestrates them — `UnitOfWork`, `Reading`, `Writing` over a shared
                 `RepositoryState`

An atomic service never imports a workflow; a workflow imports no other workflow but
`RepositoryState`.
"""

# Context: imported for its listeners — every session of the process honours the data filters
# from the first import of the ORM, before any `Database` exists.
from sincpro_framework.data_layer.orm.sqlalchemy.services import data_filtering  # noqa: F401
