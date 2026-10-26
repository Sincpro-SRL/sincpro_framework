"""Where a bounded context is hosted: an address, typed, and the context map that names one per
context.

    # the conf file — validated when it is loaded       # or the environment, which wins per context
    context_map:                                        SINCPRO_CONTEXT_MAP="billing=grpc://b:50051?timeout=5"
      - context: billing
        at: grpc://billing-service:50051?timeout=5
      - context: catalog
        at: http://catalog-service:8000

    HostedAt.parse("grpc://billing-service:50051?timeout=5")
    →  HostedAt(scheme=Scheme.GRPC, address="billing-service:50051", timeout=5.0)
    HostedAt(Scheme.HTTPS, "catalog.example:443")         the same, built in code
    HostedAt.parse("grpc://billing-service:50051?circuit_breaker=on")      calls stop while it does not answer

Context: an address is written as a URL — the one string an environment variable holds, the
same in the conf file — and read into a typed, immutable `HostedAt` as SQLAlchemy reads a database
URL into its `URL`: the scheme names the wire, the query the options. What cannot be reached is
refused when it is read, saying what was written and what to write instead. The settings declare
the context map with `HostedContext`, so this module imports nothing of the framework beyond its
own errors — the standard library and pydantic, and the logger the settings use.
"""

import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

from sincpro_framework.remote_execution.domain import exceptions

logger = logging.getLogger("sincpro_framework")

DEFAULT_TIMEOUT = 30.0
"""Seconds a call may take when the address does not say."""
DEFAULT_CIRCUIT_BREAKER_FAILURES = 5
"""Calls in a row that did not reach the context before a circuit breaker that is on opens."""
DEFAULT_CIRCUIT_BREAKER_OPEN_FOR = 30.0
"""Seconds an open circuit fails calls at once before it lets one try."""

OPTIONS = (
    "timeout",
    "circuit_breaker",
    "circuit_breaker_failures",
    "circuit_breaker_open_for",
)
"""The options an address may carry in its query."""

SWITCHES = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}
"""How `circuit_breaker=` is written: on or off."""


class Scheme(StrEnum):
    """How a context is reached — the scheme of its address: grpc, http or https."""

    GRPC = "grpc"
    HTTP = "http"
    HTTPS = "https"


WRITE = "write grpc://<host>:<port>, http://<host>:<port> or https://<host>:<port>[?timeout=<seconds>&circuit_breaker=on]"


@dataclass(frozen=True)
class HostedAt:
    """Where a bounded context is hosted: the scheme, `host:port`, each call's deadline, and —
    when asked for — the circuit breaker that stops calling it while it does not answer."""

    scheme: Scheme
    address: str
    timeout: float = DEFAULT_TIMEOUT
    circuit_breaker: bool = False
    """Stop calling the context while it does not answer (`?circuit_breaker=on`): off unless said."""
    circuit_breaker_failures: int = DEFAULT_CIRCUIT_BREAKER_FAILURES
    """Calls in a row that did not reach the context before the calls fail at once."""
    circuit_breaker_open_for: float = DEFAULT_CIRCUIT_BREAKER_OPEN_FOR
    """Seconds the calls fail at once before one is let through to try."""

    def __post_init__(self) -> None:
        """1. The scheme as a `Scheme` — refused when it is none of them.
        2. `address` as `host:port`, the port a number.
        3. A deadline of seconds above zero — a number that ends: `inf` or `nan` would fail
           later, inside a call.
        Final: a circuit breaker of one failure or more, open for seconds above zero that end — and
        tuned only when it is on, so an address never looks guarded without being so.
        """
        try:
            object.__setattr__(self, "scheme", Scheme(self.scheme))
        except ValueError:
            raise exceptions.InvalidAddress(
                f"{self.scheme!r} is not a scheme a context is reached by — grpc, http or https"
            ) from None
        host, separator, port = self.address.rpartition(":")
        if not separator or not host or not port.isdigit():
            raise exceptions.InvalidAddress(
                f"{self.address!r} is not <host>:<port> — {WRITE}"
            )
        if not self.timeout > 0:
            raise exceptions.InvalidAddress(
                f"a timeout of {self.timeout:g}s never answers — give seconds above 0"
            )
        if not math.isfinite(self.timeout):
            raise exceptions.InvalidAddress(
                f"a timeout of {self.timeout:g}s is no deadline — give a number of seconds"
            )
        if self.circuit_breaker_failures < 1:
            raise exceptions.InvalidAddress(
                f"circuit_breaker_failures={self.circuit_breaker_failures} would never close a circuit breaker — give 1 "
                "or more (circuit_breaker=off has none)"
            )
        if not self.circuit_breaker_open_for > 0:
            raise exceptions.InvalidAddress(
                f"circuit_breaker_open_for={self.circuit_breaker_open_for:g}s is no time at all — give seconds "
                "above 0"
            )
        if not math.isfinite(self.circuit_breaker_open_for):
            raise exceptions.InvalidAddress(
                f"circuit_breaker_open_for={self.circuit_breaker_open_for:g}s would never let a call try again "
                "— give a number of seconds"
            )
        tuned = (
            self.circuit_breaker_failures != DEFAULT_CIRCUIT_BREAKER_FAILURES
            or self.circuit_breaker_open_for != DEFAULT_CIRCUIT_BREAKER_OPEN_FOR
        )
        if tuned and not self.circuit_breaker:
            raise exceptions.InvalidAddress(
                "circuit_breaker_failures / circuit_breaker_open_for tune a circuit breaker that is off — add "
                "circuit_breaker=on (circuit_breaker=True in code)"
            )

    @classmethod
    def parse(cls, url: str) -> "HostedAt":
        """An address as written in the conf file or the environment.

            HostedAt.parse("grpc://10.0.0.5:50051?timeout=5")  →  HostedAt(Scheme.GRPC, "10.0.0.5:50051", 5.0)

        1. Refused, naming what was written, what no scheme reaches: no scheme or another one, no
           host, no port, a path, a timeout that is not seconds above zero.
        2. A warning for an option it does not know; the address still serves.
        3. Final: the address — `timeout` 30 seconds when not said; no circuit breaker unless
           `circuit_breaker=on`, then 5 failures and 30 seconds open unless `circuit_breaker_failures` /
           `circuit_breaker_open_for` say.
        """
        written = url.strip()
        if "://" not in written:
            raise exceptions.InvalidAddress(
                f"{written!r} names no scheme — write grpc://{written or '<host>:<port>'} "
                "(or http://, https://)"
            )
        parts = urlsplit(written)
        if parts.scheme not in tuple(Scheme):
            raise exceptions.InvalidAddress(
                f"{written!r}: {parts.scheme or 'no scheme'!r} is not a scheme a context is "
                f"reached by — {WRITE}"
            )
        if parts.path not in ("", "/"):
            raise exceptions.InvalidAddress(
                f"{written!r}: a path ({parts.path}) is not part of a context's address — {WRITE}"
            )
        options = parse_qs(parts.query)
        unknown = sorted(set(options) - set(OPTIONS))
        if unknown:
            logger.warning(
                f"{written}: {', '.join(unknown)} is not an option of an address — ignored"
            )
        switch = options.get("circuit_breaker", ["off"])[0].lower()
        if switch not in SWITCHES:
            raise exceptions.InvalidAddress(
                f"{written!r}: circuit_breaker={switch} is neither on nor off — write ?circuit_breaker=on"
            )
        failures = _number(
            written, options, "circuit_breaker_failures", DEFAULT_CIRCUIT_BREAKER_FAILURES
        )
        if not failures.is_integer():
            raise exceptions.InvalidAddress(
                f"{written!r}: circuit_breaker_failures={failures:g} is not a count — write "
                "?circuit_breaker_failures=5"
            )
        try:
            return cls(
                Scheme(parts.scheme),
                parts.netloc,
                _number(written, options, "timeout", DEFAULT_TIMEOUT, " of seconds"),
                SWITCHES[switch],
                int(failures),
                _number(
                    written,
                    options,
                    "circuit_breaker_open_for",
                    DEFAULT_CIRCUIT_BREAKER_OPEN_FOR,
                    " of seconds",
                ),
            )
        except exceptions.InvalidAddress as error:
            raise exceptions.InvalidAddress(f"{written!r}: {error}") from None

    @classmethod
    def of(cls, value: object) -> "HostedAt":
        """`value` as an address — one already built, or one written as a URL; anything else is
        refused.

            HostedAt.of(address)                    ->  address, the same object
            HostedAt.of("http://catalog:8080")      ->  HostedAt(Scheme.HTTP, "catalog:8080")
            HostedAt.of(8080)   ->  raises InvalidAddress("8080 is not an address — …")

        Context: a URL is read by `parse`, so it is refused for the same reasons it is there.
        """
        if isinstance(value, HostedAt):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise exceptions.InvalidAddress(f"{value!r} is not an address — {WRITE}")

    def __str__(self) -> str:
        """The URL it parses back from, equal — the circuit breaker only when it is on, its tuning only
        when it is not the default, every number of seconds to its last digit."""
        written = f"{self.scheme}://{self.address}?timeout={_seconds(self.timeout)}"
        if self.circuit_breaker:
            written += "&circuit_breaker=on"
        if self.circuit_breaker_failures != DEFAULT_CIRCUIT_BREAKER_FAILURES:
            written += f"&circuit_breaker_failures={self.circuit_breaker_failures}"
        if self.circuit_breaker_open_for != DEFAULT_CIRCUIT_BREAKER_OPEN_FOR:
            written += f"&circuit_breaker_open_for={_seconds(self.circuit_breaker_open_for)}"
        return written


def _seconds(value: float) -> str:
    """`value` written as the shortest text that reads back the same number — `2`, `0.1234567`."""
    return repr(float(value)).removesuffix(".0")


def _number(
    written: str, options: dict[str, list[str]], name: str, default: float, unit: str = ""
) -> float:
    """One numeric option of an address's query, or `default` when it is not written.

        "grpc://host:50051?timeout=2.5", {"timeout": ["2.5"]}, "timeout", 5.0   ->  2.5
        "…?timeout=soon", {"timeout": ["soon"]}, "timeout", 5.0   ->  InvalidAddress
            'grpc://…': timeout=soon is not a number — write ?timeout=5

    The first value wins when the option is repeated; `unit` is said in the refusal.
    """
    if name not in options:
        return default
    raw = options[name][0]
    try:
        return float(raw)
    except ValueError:
        raise exceptions.InvalidAddress(
            f"{written!r}: {name}={raw} is not a number{unit} — write ?{name}={default:g}"
        ) from None


Address = Annotated[HostedAt, PlainValidator(HostedAt.of), PlainSerializer(str)]
"""An address in a settings shape: written as a URL, validated when the shape is loaded."""


class HostedContext(BaseModel):
    """One entry of the context map: a bounded context, by its bus's name, and where it is hosted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: Annotated[str, Field(min_length=1)]
    """The bus's name — the one given to `UseFramework("billing")`."""
    at: Address

    @classmethod
    def parse_all(cls, raw: str | None) -> list["HostedContext"]:
        """The entries an environment string names, in order.

            HostedContext.parse_all("billing=grpc://b:1?timeout=5, catalog=http://c:2")
            →  [HostedContext(context="billing", at=…), HostedContext(context="catalog", at=…)]

        1. Each `<context>=<address>`, split by commas; blanks skipped.
        2. Final: refused — naming the entry — what names no context or no valid address.
        """
        entries: list[HostedContext] = []
        for entry in (raw or "").split(","):
            written = entry.strip()
            if not written:
                continue
            context, separator, address = written.partition("=")
            if not separator or not context.strip():
                raise exceptions.InvalidAddress(
                    f"{written!r} is not <context>=<address> — write billing=grpc://<host>:<port>"
                )
            try:
                at = HostedAt.parse(address)
            except exceptions.InvalidAddress as error:
                raise exceptions.InvalidAddress(f"{context.strip()}: {error}") from None
            entries.append(cls(context=context.strip(), at=at))
        return entries
