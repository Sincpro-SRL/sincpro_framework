"""What each wire speaks — REST, JSON-RPC, gRPC, MCP, FastAPI, a broker, a Lambda — and the
clock (`cron`): the library translation the gateways (`sincpro_framework.entrypoints`) build on.
A wire needs its extra installed; whoever uses one imports it.

    from sincpro_framework.entrypoints.adapters.fastapi import bus_call
    from sincpro_framework.entrypoints.adapters.cron import CronGateway

    CronGateway([billing_crons]).run()                # blocks: the clock looks and runs
"""
