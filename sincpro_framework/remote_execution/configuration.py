"""Where the framework's configuration hosts each bounded context — the conf file's `context_map`,
with `$ENV:SINCPRO_CONTEXT_MAP` winning per context.

    configured_host("billing")    →  HostedAt("grpc", "billing-service:50051", 5.0)   or None
"""

from functools import lru_cache

from sincpro_framework.remote_execution.domain.address import (
    HostedAt,
    context_map_of,
    parse_context_map,
)


@lru_cache(maxsize=1)
def _configured(
    entries: tuple[tuple[tuple[str, str], ...], ...], override: str | None
) -> dict[str, HostedAt]:
    return {**context_map_of(dict(one) for one in entries), **parse_context_map(override)}


def configured_host(context: str) -> HostedAt | None:
    """Where the framework's configuration hosts `context` — the conf file's `context_map`, with
    `$ENV:SINCPRO_CONTEXT_MAP` winning per context — or `None` when it runs here."""
    from sincpro_framework.sincpro_conf import settings

    entries = tuple(tuple(sorted(one.items())) for one in settings.context_map or ())
    return _configured(entries, settings.context_map_override).get(context)
