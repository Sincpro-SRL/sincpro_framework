"""Where a bounded context is hosted: an address, typed, and the context map that names one per
context.

    # the conf file — validated when it is loaded       # or the environment, which wins per context
    context_map:                                        SINCPRO_CONTEXT_MAP="billing=grpc://b:50051?timeout=5"
      - context: billing
        at: grpc://billing-service:50051?timeout=5
      - context: catalog
        at: http://catalog-service:8000

    HostedAt.parse("grpc://billing-service:50051?timeout=5")
    →  HostedAt(wire=Wire.GRPC, address="billing-service:50051", timeout=5.0)
    HostedAt(Wire.HTTPS, "catalog.example:443")         the same, built in code

Context: what remote execution reaches and what the settings declare, so it imports neither — the
standard library and pydantic only, the logger the settings use. An address is written as a URL — the one string an environment variable holds, the same in
the conf file — and read into a typed, immutable `HostedAt` as SQLAlchemy reads a database URL into
its `URL`: the scheme names the wire, the query the options. What cannot be reached is refused when
it is read, saying what was written and what to write instead.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

logger = logging.getLogger("sincpro_framework")

DEFAULT_TIMEOUT = 30.0
"""Seconds a call may take when the address does not say."""

OPTIONS = ("timeout",)
"""The options an address may carry in its query."""


class Wire(StrEnum):
    """The wire a context is reached by — the scheme of its address."""

    GRPC = "grpc"
    HTTP = "http"
    HTTPS = "https"


WRITE = "write grpc://<host>:<port>, http://<host>:<port> or https://<host>:<port>[?timeout=<seconds>]"


class InvalidAddress(ValueError):
    """An address no wire can reach — it names what was written and what to write instead."""


@dataclass(frozen=True)
class HostedAt:
    """Where a bounded context is hosted: the wire, `host:port`, and each call's deadline."""

    wire: Wire
    address: str
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        """1. The wire as a `Wire` — refused when it is none of them.
        2. `address` as `host:port`, the port a number.
        3. Final: a deadline above zero seconds.
        """
        try:
            object.__setattr__(self, "wire", Wire(self.wire))
        except ValueError:
            raise InvalidAddress(
                f"{self.wire!r} is not a wire a context is reached by — grpc, http or https"
            ) from None
        host, separator, port = self.address.rpartition(":")
        if not separator or not host or not port.isdigit():
            raise InvalidAddress(f"{self.address!r} is not <host>:<port> — {WRITE}")
        if not self.timeout > 0:
            raise InvalidAddress(
                f"a timeout of {self.timeout:g}s never answers — give seconds above 0"
            )

    @classmethod
    def parse(cls, url: str) -> "HostedAt":
        """An address as written in the conf file or the environment.

            HostedAt.parse("grpc://10.0.0.5:50051?timeout=5")  →  HostedAt(Wire.GRPC, "10.0.0.5:50051", 5.0)

        1. Refused, naming what was written, what no wire reaches: no scheme or another one, no
           host, no port, a path, a timeout that is not seconds above zero.
        2. A warning for an option it does not know; the address still serves.
        3. Final: the address, `timeout` 30 seconds when not said.
        """
        written = url.strip()
        if "://" not in written:
            raise InvalidAddress(
                f"{written!r} names no wire — write grpc://{written or '<host>:<port>'} "
                "(or http://, https://)"
            )
        parts = urlsplit(written)
        if parts.scheme not in tuple(Wire):
            raise InvalidAddress(
                f"{written!r}: {parts.scheme or 'no scheme'!r} is not a wire a context is "
                f"reached by — {WRITE}"
            )
        if parts.path not in ("", "/"):
            raise InvalidAddress(
                f"{written!r}: a path ({parts.path}) is not part of a context's address — {WRITE}"
            )
        options = parse_qs(parts.query)
        unknown = sorted(set(options) - set(OPTIONS))
        if unknown:
            logger.warning(
                f"{written}: {', '.join(unknown)} is not an option of an address — ignored"
            )
        try:
            return cls(Wire(parts.scheme), parts.netloc, _timeout(written, options))
        except InvalidAddress as error:
            raise InvalidAddress(f"{written!r}: {error}") from None

    @classmethod
    def of(cls, value: object) -> "HostedAt":
        """`value` as an address — one already built, or one written as a URL; anything else is
        refused."""
        if isinstance(value, HostedAt):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise InvalidAddress(f"{value!r} is not an address — {WRITE}")

    def __str__(self) -> str:
        return f"{self.wire}://{self.address}?timeout={self.timeout:g}"


def _timeout(written: str, options: dict[str, list[str]]) -> float:
    if "timeout" not in options:
        return DEFAULT_TIMEOUT
    raw = options["timeout"][0]
    try:
        return float(raw)
    except ValueError:
        raise InvalidAddress(
            f"{written!r}: timeout={raw} is not a number of seconds — write ?timeout=5"
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
                raise InvalidAddress(
                    f"{written!r} is not <context>=<address> — write billing=grpc://<host>:<port>"
                )
            try:
                at = HostedAt.parse(address)
            except InvalidAddress as error:
                raise InvalidAddress(f"{context.strip()}: {error}") from None
            entries.append(cls(context=context.strip(), at=at))
        return entries
