"""Each wire that starts the application layer from outside — REST, JSON-RPC, gRPC, MCP, FastAPI, a
FastStream broker — and the clock (`cron`). A wire needs its extra installed; whoever uses one
imports it.

    from sincpro_framework.entrypoints.adapters.fastapi import FastApiGateway
    from sincpro_framework.entrypoints.adapters.cron import CronGateway

    app = FastApiGateway([billing, sales]).app()     # returned, never started
    CronGateway([billing_crons]).run()                # blocks: the clock looks and runs
"""
