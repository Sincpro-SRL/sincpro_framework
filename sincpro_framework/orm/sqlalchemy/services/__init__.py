"""The machinery behind the repository, in two kinds, as Features and ApplicationServices are:

    atomic       one job each — the data mapper (the mapping API a project writes its tables with,
                 exported by `sincpro_framework.orm`), the SQL translator, the relation resolver,
                 the cascade, the upsert, `describe`
    workflows/   what orchestrates them — `UnitOfWork`, `Reading`, `Writing` over a shared `Store`

An atomic service never imports a workflow; a workflow imports no other workflow but `Store`.
"""
