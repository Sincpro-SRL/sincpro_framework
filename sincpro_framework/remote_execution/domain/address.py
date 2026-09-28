"""Where a bounded context is hosted: the context map, read into one address per context.

# the conf file                                  # or the environment, which wins per context
context_map:                                     SINCPRO_CONTEXT_MAP="billing=grpc://b:50051?timeout=5"
  - context: billing
    at: grpc://billing-service:50051?timeout=5
  - context: catalog
    at: http://catalog-service:8000
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from sincpro_framework.sincpro_logger import logger

DEFAULT_TIMEOUT = 30.0
"""Seconds a call may take when the address does not say."""

OPTIONS = ("timeout",)

SCHEMES = ("grpc", "http", "https")
"""The transports an address may name."""


@dataclass(frozen=True)
class HostedAt:
    """Where a bounded context is hosted: the transport, `host:port`, and each call's deadline."""

    scheme: str
    address: str
    timeout: float = DEFAULT_TIMEOUT

    def __str__(self) -> str:
        return f"{self.scheme}://{self.address}?timeout={self.timeout:g}"


def parse_address(context: str, raw: str) -> HostedAt:
    """One address, as written after `<context>=` or in `at:`.

        parse_address("billing", "grpc://10.0.0.5:50051?timeout=5")
        →  HostedAt("grpc", "10.0.0.5:50051", 5.0)

    1. Refused what no transport can reach: another scheme, or no host.
    2. A warning for an option it does not know; the address still serves.
    3. Final: the address, `timeout` 30 seconds when not said.
    """
    parts = urlsplit(raw.strip())
    if parts.scheme not in SCHEMES or not parts.netloc:
        raise ValueError(
            f"{context}: {raw!r} is not an address a context can be hosted at — write "
            f"{context}=grpc://<host>:<port> or http://<host>:<port>[?timeout=<seconds>]"
        )
    options = parse_qs(parts.query)
    unknown = sorted(set(options) - set(OPTIONS))
    if unknown:
        logger.warning(
            f"{context}: {', '.join(unknown)} is not an option of a hosted context — ignored"
        )
    timeout = float(options["timeout"][0]) if "timeout" in options else DEFAULT_TIMEOUT
    return HostedAt(parts.scheme, parts.netloc, timeout)


def parse_context_map(raw: str | None) -> dict[str, HostedAt]:
    """The contexts an environment string names, and where each is hosted.

    "billing=grpc://b:1?timeout=5, catalog=http://c:2"
    →  {"billing": HostedAt(...), "catalog": HostedAt(...)}
    """
    hosted: dict[str, HostedAt] = {}
    for entry in (raw or "").split(","):
        if not entry.strip():
            continue
        context, separator, address = entry.partition("=")
        if not separator:
            raise ValueError(
                f"{entry.strip()!r} names no address — write {entry.strip()}=grpc://<host>:<port>"
            )
        hosted[context.strip()] = parse_address(context.strip(), address)
    return hosted


def context_map_of(entries: Iterable[Mapping[str, str]]) -> dict[str, HostedAt]:
    """The contexts a `context_map:` list names — `{context: …, at: …}` each."""
    hosted: dict[str, HostedAt] = {}
    for entry in entries:
        context, address = entry.get("context"), entry.get("at")
        if not context or not address:
            raise ValueError(
                f"{dict(entry)} is not a context map entry — write {{context: billing, at: grpc://…}}"
            )
        hosted[context] = parse_address(context, address)
    return hosted
