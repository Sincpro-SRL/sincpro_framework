"""The context across a transport made of text headers — HTTP, gRPC metadata, a broker's message.

    headers = inject(use_context())         what a message or a request carries
    context = extract(headers)              what an entrance opens its flow with

What is written, and read back in this order — the later wins:

    baggage                     W3C Baggage: the travelling scalars, for OpenTelemetry and any
                                other stack (text only, ≤ 8192 bytes, ≤ 180 members)
    sincpro-context             the travelling keys as JSON — lists too (`tenant_ids`)
    x-correlation-id · x-causation-id · x-execution-id     the identity (PRD_21)
    x-context-node              the node in play, when a store shares the context (PRD_24)

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

from sincpro_framework.context.domain.execution import (
    CONTEXT_NODE,
    CONTEXT_NODE_HEADER,
    IDENTITY_HEADERS,
)
from sincpro_framework.context.domain.keys import travelling
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


def inject(context: Mapping[Any, Any]) -> dict[str, str]:
    """The headers that carry `context` — caused by the execution in play, in its flow.

    1. The text keys of `context`, handed on: the execution in play becomes the cause, its
       own `execution_id` stays here, the node in play is named when a store shares it.
    2. The identity as its own headers, and the node as `x-context-node`.
    3. Every travelling key as the `sincpro-context` JSON.
    Final: the travelling scalars as `baggage` too, when there are any.

        inject({"tenant_id": "acme"})      (inside execution E1 of flow C)
        ->  {"x-correlation-id": "C", "x-causation-id": "E1",
             "sincpro-context": '{"tenant_id":"acme","causation_id":"E1",…}',
             "baggage": "tenant_id=acme,causation_id=E1,…"}
    """
    carried = handed_on(
        {key: value for key, value in context.items() if isinstance(key, str)}
    )
    headers = {
        header: str(carried[key])
        for header, key in IDENTITY_HEADERS.items()
        if carried.get(key)
    }
    if carried.get(CONTEXT_NODE):
        headers[CONTEXT_NODE_HEADER] = str(carried[CONTEXT_NODE])
    headers[CONTEXT_HEADER] = written(carried)
    members = baggage(carried)
    if members:
        headers[BAGGAGE_HEADER] = members
    return headers


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
