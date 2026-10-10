"""What several packages of the framework share and none of them owns — each in its own context.

common.ids         the ids the framework mints: UUID v7, for every entity, event and execution
common.ordering    the one order of whatever hangs on one point: hooks, interceptors, crons
common.naming      what a registered class is known by: an event its `name`, any DTO
                   `context.Class`
common.failures    what kind of failure an error is, and how every wire tells its caller
common.serialization  any value as JSON values and back — every message the framework sends
common.store       the storage contracts every component shares (`KeyValueStore`)
common.transport   the mechanics of each technology: addresses, the gRPC server
"""
