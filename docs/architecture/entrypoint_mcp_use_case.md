# `entrypoint_mcp` — use case: `sincpro_siat_soap`

This is not the README Greeting example. The contract lives in [entrypoint_mcp.md](./entrypoint_mcp.md). This page evaluates **`entrypoint_mcp`** against the SDK Sincpro actually ships: **SIAT SOAP**. The same pattern applies to `sincpro_payments_sdk` / CyberSource.

**Verdict:** the framework extra is ready. What remains is a **one-file host in the SDK** plus an `exclude` list. Features do not change.

---

## Actor and job

A developer (or an agent in Cursor) working on invoicing for a Bolivian tenant. Today they import `siat_soap_sdk` and call:

```python
from sincpro_siat_soap import siat_soap_sdk
from sincpro_siat_soap.services.billing.verify_invoice_state import (
    CommandVerifyInvoiceState,
    ResponseVerifyInvoiceState,
)

result = siat_soap_sdk(CommandVerifyInvoiceState(...), ResponseVerifyInvoiceState)
```

The job with `entrypoint_mcp`: the same operation as a tool, so Cursor / Claude / LangChain can **generate CUF, ask SIAT for CUFD, verify invoice state** without rewriting SOAP.

That is the product: the bus is already the API; `entrypoint_mcp` is the MCP host.

---

## What exists today in the SDK (no MCP yet)

`siat_soap_sdk = config_framework("siat-soap-sdk")` then ~70 `@siat_soap_sdk.feature` / `@siat_soap_sdk.app_service` registrations across:

| Bounded slice | Examples |
|---|---|
| Auth | `CommandGenerateCUFD`, `CommandGenerateCUIS`, `CommandVerifyNit`, `CommandExtractP12Credentials` |
| Digital files | `CommandGenerateCUF`, `CommandDecodeCUF`, `CmdGenerateXML`, `CommandSignXML`, `CommandCompressFile` |
| Billing | `CommandInvoiceReceptionRequest`, `CommandVerifyInvoiceState`, `CommandSendInvoicePackage` |
| Operations | POS create/close, significant events, health check |
| Sync | catalogs, legends, date |

Python host is done. `entrypoint_mcp` is **not** wired in the SDK repo yet. The framework extra is what this document is for.

---

## Session (happy path the agent can run)

JSON-safe Features. No `bytes`. Same `execute` as production.

```text
1. CommandGenerateCUFD   → cufd + control_code (ApplicationService)
2. CommandGenerateCUF    → cuf string (Feature, pure encoding)
3. CommandVerifyInvoiceState → literal_status + reception_code
```

Cursor config (stdio), after the SDK adds a thin module:

```json
{
  "mcpServers": {
    "siat-soap": {
      "command": "python",
      "args": ["-m", "sincpro_siat_soap.entrypoint_mcp"]
    }
  }
}
```

Remote agents: the same process with `run(transport="http", port=8000)` → `http://127.0.0.1:8000/mcp`. Still MCP (`tools/call`), not REST.

---

## SDK module to add (only new code in SIAT)

Do **not** put this in `domain/` or on Feature classes.

```python
# sincpro_siat_soap/entrypoint_mcp.py
from sincpro_framework.entrypoints.mcp import Entrypoint
from sincpro_siat_soap import siat_soap_sdk
from sincpro_siat_soap.services.auth_permissions.extract_p12_credentials import (
    CommandExtractP12Credentials,
)
from sincpro_siat_soap.services.auth_permissions.revoce_certs import CommandRevokeCerts

def main() -> None:
    Entrypoint(siat_soap_sdk).exclude(
        CommandExtractP12Credentials,
        CommandRevokeCerts,
    ).run()

if __name__ == "__main__":
    main()
```

`pip install sincpro-framework[mcp]` on the SDK extra. Features stay as they are.

Default `build_mcp_server(siat_soap_sdk).run()` also works. `exclude` is required in production because **revoking certificates is JSON-safe** and would otherwise become a tool.

---

## Catalog policy (what MCP should and must not publish)

The skip of `bytes` is automatic. Destructive or secret operations are **not**.

| DTO / Feature | MCP? | Why |
|---|---|---|
| `CommandGenerateCUF` | yes | Primitives + `datetime \| str`. Pure function. Ideal agent tool. |
| `CommandDecodeCUF` | yes | Same. |
| `CommandGenerateCUFD` / `CommandGenerateCUIS` | yes | JSON fields; talks to SIAT. Needs network + credentials already in the SDK adapters. |
| `CommandVerifyInvoiceState` | yes | Query. JSON in, status out. |
| `CommandVerifyNit` | yes | Query. |
| Health checks | yes | Cheap. Good smoke test for the host. |
| `CommandInvoiceReceptionRequest` | **skipped** | `xml: bytes`. Framework omits the tool. |
| `CommandSendInvoicePackage` | **skipped** | `xml: bytes`. |
| `CommandCompressFile` | **skipped** | Response is a zip blob (`Any` / binary). |
| `CommandExtractP12Credentials` | **skipped + exclude** | `p12_payload: bytes \| str` is skipped for `bytes`; if someone passes a PEM `str`, **exclude anyway** (private key out). |
| `CommandRevokeCerts` | **exclude** | JSON-safe and **destructive**. Must not be a default tool. |
| `CmdGenerateXML` / `CommandSignXML` | case by case | XML as `str` can be a tool; keep payloads small. Signing may need the cert already in process memory (adapter), not in the MCP payload. |

Binary invoice send stays **Python/Odoo**: generate XML in-process, then `siat_soap_sdk(CommandInvoiceReceptionRequest(xml=bytes, ...))`. The agent can still own CUF / CUFD / verify.

Do not invent base64-on-the-DTO in the framework. If SIAT later wants packages over MCP, that is an SDK `wrap` or a string field — not `expose_mcp` on the Feature.

---

## Gaps that show up only on a real SDK (not Greeting)

### 1. Feature docstrings

`GenerateCUF` has comments inside the class, not a class `__doc__`. MCP instruction falls back to `CommandGenerateCUF`. Agents get a name, not a SIAT explanation.

**SDK work:** one-line class docstring on Features that should be tools. The framework will not inherit the `Feature` base essay (already tested).

### 2. `raw_response: Any`

Many response DTOs keep the Zeep SOAP object. `model_dump(mode="json")` can fail or emit garbage. The Feature still succeeded.

**SDK work:** agents need `cuf`, `cufd`, `reception_code`, `literal_status`. Leave `raw_response` out of the JSON dump or type it as `dict`. That is a DTO cleanup, independent of MCP, but MCP makes it visible.

### 3. Enums (`SIATEnvironment`, `SIATModality`)

Pydantic + FastMCP 3 already map enums to JSON Schema. Agents must send the **value** (`1`), not the Python name. Document that in the Feature docstring.

### 4. Side effects and SIAT rate limits

`CommandGenerateCUFD` hits Impuestos. An agent looping tools is a production incident. `wrap` on the SDK side (audit log, allow-list of NITs) is the place — not a framework flag.

### 5. HTTP without auth

`run(transport="http")` exposes every published tool on the LAN. Stdio is the default for Cursor. HTTP is for a locked process (VPN, FastMCP auth), not a public tax API.

---

## Payments / CyberSource (same evaluation, shorter)

README already uses tokenization + payment orchestration. MCP maps:

| Python | MCP tool |
|---|---|
| `cybersource(TokenizationParams(...))` | `TokenizationParams` |
| `cybersource(PaymentServiceParams(...))` | `PaymentServiceParams` (ApplicationService) |

Value Objects (`NIT`, `Email`) already round-trip in framework tests. Exclude refunds / void / capture-admin if those DTOs are JSON-safe and too dangerous for an agent.

---

## Fit score

| Question | Answer |
|---|---|
| Does SIAT need a rewrite? | No. |
| Can an agent generate CUF / CUFD / verify today once the SDK adds `entrypoint_mcp`? | Yes. |
| Can an agent `recepcionFactura` with XML bytes? | No, by design. |
| Is REST `POST /invoices` included? | No. That is not `entrypoint_mcp`. |
| Is the framework Greeting example enough to ship SIAT? | No. This page is the missing use case. |
| Remaining framework work for this use case? | None required. Remaining work is SDK: module, `exclude`, docstrings, response DTO hygiene. |

---

## How to prove it on SIAT

1. In `sincpro_siat_soap`: extra `[mcp]`, module above, `exclude` P12 + revoke.
2. `python -m sincpro_siat_soap.entrypoint_mcp` (stdio).
3. Cursor: call `CommandGenerateCUF` with a known NIT fixture from `test/`.
4. Call `CommandVerifyInvoiceState` against SIAT test environment (same credentials the SDK already uses).
5. Confirm `CommandInvoiceReceptionRequest` is **absent** from `tools/list`.
6. Confirm `CommandRevokeCerts` is **absent**.
