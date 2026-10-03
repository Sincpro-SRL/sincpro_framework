"""Where this process's configuration hosts each bounded context — the conf file's `context_map`,
with the environment's `SINCPRO_CONTEXT_MAP` winning per context.

    configured_host("billing")    →  HostedAt(Wire.GRPC, "billing-service:50051", 5.0)   or None

Context: a context runs here unless this process's configuration says otherwise. Each deployment
names only what *it* reaches elsewhere — the service hosting `billing` does not name it, so its
server, its workers and its crons all run it. The conf file's entries are validated when it is
loaded; the environment is read whether or not the project's own conf file declares
`$ENV:SINCPRO_CONTEXT_MAP` — a deployment that sets it is never ignored — and an address it gets
wrong is refused naming the variable.
"""

import os
from functools import lru_cache

from sincpro_framework.transport.addresses import (
    HostedAt,
    HostedContext,
    InvalidAddress,
)

CONTEXT_MAP_ENV = "SINCPRO_CONTEXT_MAP"


@lru_cache(maxsize=1)
def _configured(
    from_file: tuple[HostedContext, ...], from_environment: str | None
) -> dict[str, HostedAt]:
    """Each context's address — the conf file's entries, then the environment's, the later
    winning."""
    try:
        overriding = HostedContext.parse_all(from_environment)
    except InvalidAddress as error:
        raise InvalidAddress(f"{CONTEXT_MAP_ENV}: {error}") from None
    return {entry.context: entry.at for entry in (*from_file, *overriding)}


def configured_host(context: str) -> HostedAt | None:
    """Where this process's configuration hosts `context` — the conf file's `context_map`, with
    `SINCPRO_CONTEXT_MAP` winning per context — or `None` when it runs here."""
    from sincpro_framework.sincpro_conf import settings

    from_environment = os.environ.get(CONTEXT_MAP_ENV) or settings.context_map_override
    return _configured(tuple(settings.context_map), from_environment).get(context)
