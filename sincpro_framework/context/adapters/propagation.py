"""The context across a transport made of text headers — HTTP, gRPC metadata, a broker's message.

    headers = inject(use_context())         what a message or a request carries
    context = extract(headers)              what an entrance opens its flow with

What is written, and read back in this order — the later wins:

    baggage                     W3C Baggage: the travelling scalars, for OpenTelemetry and any
                                other stack (text only, ≤ 8192 bytes, ≤ 180 members)
    sincpro-context             the travelling keys as JSON — lists too (`tenant_ids`)
    x-correlation-id · x-causation-id · x-execution-id     the identity (PRD_21)

`inject` writes the execution in play as the cause of whatever receives it. Only what travels is
written: text, numbers, booleans and lists of them, a `Secret` as its value — never an object or
a connection.
`remote_execution` carries the whole context its own way (one packed header): both ends are this
codebase.
"""

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

from sincpro_framework.context.domain.execution import IDENTITY_HEADERS
from sincpro_framework.context.domain.keys import standardized, travelling
from sincpro_framework.context.infrastructure.tree import handed_on
from sincpro_framework.sincpro_logger import logger

CONTEXT_HEADER = "sincpro-context"
BAGGAGE_HEADER = "baggage"
BAGGAGE_BYTES = 8192
BAGGAGE_MEMBERS = 180


def written(context: Mapping[Any, Any]) -> str:
    """The travelling keys of `context` as the one JSON header a message carries."""
    return json.dumps(travelling(context), separators=(",", ":"))


def read(header: str | None) -> dict[str, Any]:
    """The context a `sincpro-context` header carried; empty when there is none or it does not
    parse."""
    if not header:
        return {}
    try:
        found = json.loads(header)
    except ValueError:
        logger.warning(f"a {CONTEXT_HEADER} header that is not JSON was left out")
        return {}
    return standardized(found) if isinstance(found, dict) else {}


def baggage(context: Mapping[Any, Any]) -> str:
    """The travelling scalars as a W3C `baggage` value; what passes its limits is left out."""
    members: list[str] = []
    size = 0
    for key, value in travelling(context).items():
        if isinstance(value, list):
            continue
        member = f"{quote(key, safe='')}={quote(str(value), safe='')}"
        if len(members) == BAGGAGE_MEMBERS or size + len(member) + 1 > BAGGAGE_BYTES:
            logger.warning(f"the context key '{key}' passed the W3C baggage limits: left out")
            break
        members.append(member)
        size += len(member) + 1
    return ",".join(members)


def unbaggaged(header: str | None) -> dict[str, str]:
    """The members of a W3C `baggage` value; properties after `;` are ignored."""
    found: dict[str, str] = {}
    for member in (header or "").split(","):
        name, separator, value = member.split(";", 1)[0].partition("=")
        if separator and name.strip():
            found[unquote(name.strip())] = unquote(value.strip())
    return found


def inject(context: Mapping[Any, Any]) -> dict[str, str]:
    """The headers that carry `context` — caused by the execution in play, in its flow."""
    carried = handed_on(
        {key: value for key, value in context.items() if isinstance(key, str)}
    )
    headers = {
        header: str(carried[key])
        for header, key in IDENTITY_HEADERS.items()
        if carried.get(key)
    }
    headers[CONTEXT_HEADER] = written(carried)
    members = baggage(carried)
    if members:
        headers[BAGGAGE_HEADER] = members
    return headers


def extract(headers: Mapping[str, Any]) -> dict[str, Any]:
    """The context a request or a message carried — `baggage`, then `sincpro-context`, then the
    identity headers, the later winning. Header names are read in any case."""
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
    return standardized(found)
