"""The context across a transport made of text headers — HTTP, gRPC metadata, a broker's message.

    headers(handed_on(context))             what a message or a request carries
    extract(headers)                        what an entrance opens its flow with

What is written, and read back in this order — the later wins:

    baggage                     W3C Baggage: the travelling scalars, for OpenTelemetry and any
                                other stack (text only, ≤ 8192 bytes, ≤ 180 members)
    sincpro-context             the travelling keys as JSON — lists too (`tenant_ids`)
    x-correlation-id · x-causation-id · x-execution-id     the identity (PRD_21)
    x-context-node              the node in play, when a store shares the context (PRD_24)

`headers` writes a context already handed on — the execution in play as its cause
(`services.propagation.inject` hands it on first). Only what travels is written: text, numbers,
booleans and lists of them, a `Secret` as its value — never an object or a connection.
`remote_execution` carries the flow its own way (one packed header, with its types): both ends are
this codebase.
"""

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

from sincpro_framework.context.domain.keys import CONTEXT_NODE, ContextKey
from sincpro_framework.context.infrastructure.travel import travelling
from sincpro_framework.sincpro_logger import logger

CONTEXT_HEADER = "sincpro-context"
BAGGAGE_HEADER = "baggage"
CONTEXT_NODE_HEADER = "x-context-node"
"""The header a message carries `CONTEXT_NODE` in — so a receiver needs no other header to read the
sender's chain."""
IDENTITY_HEADERS: dict[str, str] = {
    "x-correlation-id": ContextKey.CORRELATION_ID,
    "x-causation-id": ContextKey.CAUSATION_ID,
    "x-execution-id": ContextKey.EXECUTION_ID,
}
"""The headers an entrypoint reads the identity from — HTTP and gRPC alike, lowercase."""
BAGGAGE_BYTES = 8192
BAGGAGE_MEMBERS = 180
CONTEXT_BYTES = 4096
"""How much `sincpro-context` is expected to weigh at most — half of what gRPC metadata, a W3C
baggage and an nginx header buffer take by default (8 KiB), the rest left to the trace, the
identity and the transport. Past it, a warning: a value that large belongs in a store, the
context carrying its id."""

_oversized: set[str] = set()
"""The keys already warned about as the largest of a context past `CONTEXT_BYTES`."""


def written(context: Mapping[Any, Any]) -> str:
    """The travelling keys of `context` as the one JSON header a message carries."""
    return json.dumps(travelling(context), separators=(",", ":"))


def read(header: str | None) -> dict[str, Any]:
    """The context a `sincpro-context` header carried; empty when there is none or it does not
    parse.

        read('{"tenant_id":"acme","tenant_ids":["acme","beta"]}')
            ->  {"tenant_id": "acme", "tenant_ids": ["acme", "beta"]}
        read("not json")    ->  {}, and a warning
        read('["acme"]')    ->  {}                  # JSON, but not an object

    Read as it came: nothing is checked against what may travel — `travelling` did that."""
    if not header:
        return {}
    try:
        found = json.loads(header)
    except ValueError:
        logger.warning(f"a {CONTEXT_HEADER} header that is not JSON was left out")
        return {}
    return found if isinstance(found, dict) else {}


def baggage(context: Mapping[Any, Any]) -> str:
    """The travelling scalars as a W3C `baggage` value; what passes its limits is left out.

        baggage({"tenant_id": "acme", "company": 3, "tenant_ids": ["acme"], "note": "a b"})
            ->  "tenant_id=acme,company=3,note=a%20b"

    1. The keys that may travel (`travelling`), in order.
    2. For each:
       2.1 a list or a mapping is skipped — baggage holds text;
       2.2 the member `key=value`, both percent-encoded;
       2.3 a member past 180 members or 8192 bytes: warned about, and it and every key
           after it are left out.
    Final: the members joined by commas — `""` when none.
    """
    members: list[str] = []
    size = 0
    for key, value in travelling(context).items():
        if isinstance(value, (list, dict)):
            continue
        member = f"{quote(key, safe='')}={quote(str(value), safe='')}"
        if len(members) == BAGGAGE_MEMBERS or size + len(member) + 1 > BAGGAGE_BYTES:
            logger.warning(f"the context key '{key}' passed the W3C baggage limits: left out")
            break
        members.append(member)
        size += len(member) + 1
    return ",".join(members)


def unbaggaged(header: str | None) -> dict[str, str]:
    """The members of a W3C `baggage` value; properties after `;` are ignored.

        unbaggaged("tenant_id=acme;ttl=1, note=a%20b,broken")
            ->  {"tenant_id": "acme", "note": "a b"}

    Names and values percent-decoded and stripped; a member without `=` is left out."""
    found: dict[str, str] = {}
    for member in (header or "").split(","):
        name, separator, value = member.split(";", 1)[0].partition("=")
        if separator and name.strip():
            found[unquote(name.strip())] = unquote(value.strip())
    return found


def headers(carried: Mapping[str, Any]) -> dict[str, str]:
    """The headers that carry `carried` — a context already handed on (`handed_on`), its
    identity and the node in play among its keys.

    1. The identity as its own headers, and the node as `x-context-node`.
    2. Every travelling key as the `sincpro-context` JSON — past `CONTEXT_BYTES`, warned about
       once per largest key, and written all the same.
    Final: the travelling scalars as `baggage` too, when there are any.

        headers({"tenant_id": "acme", "correlation_id": "C", "causation_id": "E1"})
        ->  {"x-correlation-id": "C", "x-causation-id": "E1",
             "sincpro-context": '{"tenant_id":"acme","correlation_id":"C",…}',
             "baggage": "tenant_id=acme,correlation_id=C,…"}
    """
    found = {
        header: str(carried[key])
        for header, key in IDENTITY_HEADERS.items()
        if carried.get(key)
    }
    if carried.get(CONTEXT_NODE):
        found[CONTEXT_NODE_HEADER] = str(carried[CONTEXT_NODE])
    found[CONTEXT_HEADER] = written(carried)
    size = len(found[CONTEXT_HEADER].encode())
    if size > CONTEXT_BYTES:
        largest = max(travelling(carried).items(), key=lambda item: len(json.dumps(item[1])))[
            0
        ]
        if largest not in _oversized:
            _oversized.add(largest)
            logger.warning(
                f"the context carried weighs {size} bytes, past {CONTEXT_BYTES}: '{largest}' "
                "is its largest key — save a large value in a store and carry its id; gRPC and "
                "most proxies refuse headers past 8 KiB"
            )
    members = baggage(carried)
    if members:
        found[BAGGAGE_HEADER] = members
    return found


def extract(headers: Mapping[str, Any]) -> dict[str, Any]:
    """The context a request or a message carried — `baggage`, then `sincpro-context`, then the
    identity headers, the later winning. Header names are read in any case.

        extract({"Baggage": "lang=es", "sincpro-context": '{"tenant_ids":["a","b"]}',
                 "X-Correlation-Id": "C"})
        ->  {"lang": "es", "tenant_ids": ["a", "b"], "correlation_id": "C"}
    """
    lowered = {str(name).lower(): value for name, value in headers.items()}
    found: dict[str, Any] = dict(unbaggaged(lowered.get(BAGGAGE_HEADER)))
    found.update(read(lowered.get(CONTEXT_HEADER)))
    found.update(
        {
            key: lowered[header]
            for header, key in IDENTITY_HEADERS.items()
            if lowered.get(header)
        }
    )
    if lowered.get(CONTEXT_NODE_HEADER):
        found[CONTEXT_NODE] = lowered[CONTEXT_NODE_HEADER]
    return found
