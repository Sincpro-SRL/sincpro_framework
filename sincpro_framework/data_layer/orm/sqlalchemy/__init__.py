"""The SQLAlchemy backend, in the layers every framework component uses. A layer imports only
the ones below it (`tests/data_layer/orm/test_layers.py`):

    entrypoint/       `Repository` — the facade a Feature receives, composed of the workflows;
                      `map_aggregates`, `map_events` and the template tables a project declares
    services/         workflows/ — `UnitOfWork`, `Reading`, `Writing` over the
                      `RepositoryState` they share,
                      orchestrating the atomic services: the data mapper, the SQL translator, the
                      relation resolver, the cascade, the upsert, `ModelIntrospection`
    adapters/         what other components ask of a database: `DatabaseNumbering`,
                      `SqlUseCases`, `AlembicEngine`
    domain/           the adapter's vocabulary, no I/O: `Held`, `HeldRelations`, `Orphans`,
                      `Isolation`, `Transaction`, the column types
    infrastructure/   `Database` and what every read and write goes through: the mapping
                      registry, the engine's errors named, the hooks of a transaction,
                      observability, change and read tracking

The public API is what `sincpro_framework.data_layer.orm` exports; the paths inside are the
framework's own. Everything here knows SQLAlchemy; nothing in `sincpro_framework.ddd` does.
"""
