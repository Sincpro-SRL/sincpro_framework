"""The client contract of what gateways publish: per bounded context, one document — what each
operation is and what it takes and answers (the metamodel), beside how each wire reaches it —,
joined by the Command's identity (PRD_33 §3). A client generates its domain and its client from
it; the framework writes JSON, never a client.

    client_contracts([rest, rpc])
        ->  {"billing": {"format": "sincpro.client-contract/1", "bounded_context": "acme-billing",
                         "alias": "billing", "version": "9b1c…",
                         "models": {…}, "operations": {…}, "$defs": {…},
                         "bindings": {
                             "rest": {"billing.CommandIssueInvoice": {"method": "POST",
                                      "path": "/billing/invoices", "path_fields": []}},
                             "rpc": {"billing.CommandIssueInvoice":
                                     {"method": "billing.issue_invoice"}}}}}
    export_client_contract([rest, rpc], "contracts")
        ->  {"billing": Path("contracts/billing.contract.json")}

Context: `bounded_context` is the bounded context's own name — what every identity starts with
(`acme-billing.CommandIssueInvoice`) —, never the runtime context a use case reads; `alias` the
name the gateways publish it under
(`FastApiGateway({"billing": billing})`) — what a client names the context by, what the file and
the live route are named after. Only what a gateway exposes is in it — an `@internal` use case is in no contract, and a
wire that does not publish an operation has no binding for it. The same gateways write the same
bytes: `version` is the fingerprint of the whole document, what a client compares its snapshot
with; each model keeps the version of its own (`model_version`), what a read answers.
"""

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.common.naming import local_name
from sincpro_framework.entrypoints.domain.surface import ManifestEntry
from sincpro_framework.entrypoints.services.gateway import Gateway
from sincpro_framework.introspection import metamodel_of
from sincpro_framework.use_bus import UseFramework

FORMAT = "sincpro.client-contract/1"
"""What a client reads the document as; a change to its shape that breaks a reader is another
number."""


def _deprecation(entries: Iterable[ManifestEntry]) -> dict[str, Any]:
    """An operation's deprecation as every wire publishing it says it: deprecated, with its
    sunset and its replacement, when each one says the same; not otherwise — each binding then
    carries its own.

        [rest: deprecated, sunset 2027-01-01; rpc: the same]
            ->  {"deprecated": True, "sunset": "2027-01-01", "replacement": "billing.QueryGet"}
        [rest: deprecated; rpc: not]
            ->  {"deprecated": False, "sunset": None, "replacement": None}
    """
    said = {
        (one.deprecated, one.sunset.isoformat() if one.sunset else None, one.replacement)
        for one in entries
    }
    deprecated, sunset, replacement = said.pop() if len(said) == 1 else (False, None, None)
    return {"deprecated": deprecated, "sunset": sunset, "replacement": replacement}


def _binding(entry: ManifestEntry, address: dict[str, Any]) -> dict[str, Any]:
    """How a wire reaches an operation (`Gateway.addresses`), and its deprecation on that wire
    when it has one."""
    if not entry.deprecated:
        return address
    return {
        **address,
        "deprecated": True,
        "sunset": entry.sunset.isoformat() if entry.sunset else None,
        "replacement": entry.replacement,
    }


def client_contracts(gateways: Gateway | Iterable[Gateway]) -> dict[str, dict[str, Any]]:
    """The client contract of each bounded context `gateways` publish — one gateway per wire,
    or a single one —, by the alias they publish it under.

        client_contracts(rest)            ->  {"billing": {…, "bindings": {"rest": {…}}}}
        client_contracts([rest, rpc])     ->  {"billing": {…, "bindings": {"rest": …, "rpc": …}}}

    1. Each gateway's surface: its manifest lines (`manifest`) and how its wire reaches each
       operation (`addresses`), by the alias of their context; an alias that names two bounded
       contexts across the gateways is refused.
    2. Per context, the metamodel of what the gateways expose (`metamodel_of`): what a client
       calls, each model as its contract, what they share written once.
    3. Each operation with its deprecation (`_deprecation`); each wire's bindings, of the
       operations in the contract, by the same identity (`_binding`).
    Final: the document — the bounded context's own name and its alias —, `version` the
    fingerprint of all of it.
    """
    listed = [gateways] if isinstance(gateways, Gateway) else list(gateways)
    buses: dict[str, UseFramework] = {}
    published: dict[str, list[tuple[ManifestEntry, dict[str, Any]]]] = {}
    for gateway in listed:
        catalogs, addresses = gateway.catalogs, gateway.addresses()
        for entry in gateway.manifest():
            bus = catalogs[entry.context].framework_instance
            held = buses.setdefault(entry.context, bus)
            if held is not bus:
                raise exceptions.ProgrammingError(
                    f"{entry.context!r} publishes {held.name} on one gateway and {bus.name} on "
                    f"another: an alias names one bounded context in a client contract"
                )
            published.setdefault(entry.context, []).append((entry, addresses[entry.command]))
    contracts: dict[str, dict[str, Any]] = {}
    for alias in sorted(published):
        lines, bounded_context = published[alias], buses[alias].name
        metamodel = metamodel_of(
            buses[alias], {local_name(entry.command, bounded_context) for entry, _ in lines}
        )
        operations = metamodel["operations"]
        for identity, operation in operations.items():
            operation.update(
                _deprecation(entry for entry, _ in lines if entry.command == identity)
            )
        bindings: dict[str, dict[str, Any]] = {}
        for entry, address in lines:
            if entry.command in operations:
                bindings.setdefault(entry.wire, {})[entry.command] = _binding(entry, address)
        content = {
            "format": FORMAT,
            "bounded_context": bounded_context,
            "alias": alias,
            "models": metamodel["models"],
            "operations": operations,
            "$defs": metamodel["$defs"],
            "bindings": bindings,
        }
        written = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        contracts[alias] = {
            "version": hashlib.sha256(written.encode()).hexdigest(),
            **content,
        }
    return contracts


def contract_bytes(contract: dict[str, Any]) -> bytes:
    """A contract as it is written and served — keys sorted, indented, a final newline: the same
    document is the same bytes, so a diff shows only what changed."""
    text = json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    return (text + "\n").encode("utf-8")


def export_client_contract(
    gateways: Gateway | Iterable[Gateway], out_dir: str | Path
) -> dict[str, Path]:
    """Write each bounded context's contract to `<out_dir>/<alias>.contract.json` — for a
    build or a CI step, nothing served — and answer where each one went, by alias.

        export_client_contract([rest, rpc], "web/contracts")
            ->  {"billing": Path("web/contracts/billing.contract.json"), "stock": Path(…)}
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for alias, contract in client_contracts(gateways).items():
        path = directory / f"{alias}.contract.json"
        path.write_bytes(contract_bytes(contract))
        written[alias] = path
    return written


def contract_of(gateway: Gateway, alias: str) -> tuple[bytes, str] | None:
    """What a running gateway serves for the context it publishes as `alias`: the bytes its
    export would write, and the version that tags them — `None` for an alias it does not publish. Built when asked, so a
    use case registered while it runs, or a model an extension changed, is the contract at once.

        contract_of(rest, "billing")    ->  (b'{\n  "$defs": …}\n', "9b1c…")
        contract_of(rest, "nowhere")    ->  None
    """
    found = client_contracts(gateway).get(alias)
    return None if found is None else (contract_bytes(found), found["version"])
