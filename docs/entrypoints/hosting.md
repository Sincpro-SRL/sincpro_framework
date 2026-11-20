# Hosting: one host per product, `/api/<door>` for the service

The gateways serve any path — `group(prefix=, version=)`, `files_path=`, `http_app(path=)` — and
nothing here requires the layout below. It is what Sincpro recommends, so that a web, an agent
and an integration find every product's service the same way, and so that a door can move to a
process of its own without a URL changing.

## The layout

```
acme.sincpro.com.bo
  /api/rest/v1/…        REST — FastApiGateway; its discovery document at /api/rest/v1/openapi.json
  /api/mcp              MCP — McpGateway, Streamable HTTP
  /api/files/{token}    a record's file, by the signed link its answer carried
  /api/media/{key}      public pictures by a stable key
  /api/hooks/{source}   what an outside system calls back — a bank, a payment QR — when there is
  /api/events           realtime (SSE, WebSocket) — when there is
  /.well-known/…        what a standard puts at a host's root (the MCP's OAuth metadata, app links)
  /*                    the web — Next.js or a single-page application (web-framework)
```

1. **`/api` is the service's, everything else is the web's.** The web never serves under `/api`:
   in Next.js, no `app/api/`; a handler of the web's own (revalidating a cached page) goes under
   `/_web/`. The ingress has one rule per side and the two never collide.
2. **The second segment names the door** — a protocol, or a kind of client — never a business
   resource. Invoices, payments and members live inside a door (`/api/rest/v1/billing/…`), never
   beside it.
3. **Each door versions its own way.** REST in its path (`/api/rest/v1`, a `/api/rest/v2` beside
   it while clients move); MCP in its protocol (`MCP-Protocol-Version`); a hook as its source
   sends it. What a page keeps has no version: a picture's URL in a cached page must outlive
   `rest/v2`.
4. **Each door authenticates its own way.** REST and MCP with a bearer; a file by its signed
   link; media are public; a hook by the signature of the system that calls. MCP and hooks never
   take the web's cookie — a page of another site must not be able to call them as the person.
5. **What is for operators stays at the process's root, and the ingress never routes it:**
   `/healthz`, `/readyz`, `/metrics` ([readiness.md](readiness.md)). Kubernetes' probes and the
   scrape reach the pod, never the internet. `/client-contract` too, unless clients outside the
   cluster read it (`contract_path=` moves it under `/api/rest`).

## Why one host, and not an `api.` subdomain

- **One origin.** A browser sends a request with `Authorization` to another origin only after a
  preflight; on the same host there is none. When the web keeps a session in a cookie, it is a
  plain `HttpOnly` cookie of the host — across subdomains it becomes a cookie of the parent domain
  with CSRF guards around it.
- **One certificate and one name per tenant** (`<tenant>.sincpro.com.bo`), not one per door.
- **Separating deployments needs no host.** The ingress routes by path: each door can be a
  Service of its own, with its own timeout, rate limit and cache. MCP keeps long connections open
  and scales apart — it can run in a process of its own from the first day and still answer at
  `/api/mcp`.

A host of its own is for a different **trust boundary**, not for a different door: a sign-in
several products share (`auth.`), an MCP several products share (`mcp.internal.sincpro.com.bo`),
an administration that must sit behind a VPN. An MCP of one product lives at `/api/mcp` of that
product's host.

## Wiring it

REST, files and the discovery document — a group's `prefix` replaces `/{alias}` and `version` is
put before it, so the version is written into the prefix:

```python
from sincpro_framework.entrypoints import FastApiGateway

REST = "/api/rest/v1"

api = FastApiGateway([billing, sales])
api.group(billing, prefix=f"{REST}/billing")   # /api/rest/v1/billing/…
api.group(sales, prefix=f"{REST}/sales")
app = api.app(
    files=storage,                             # a record's files, served by their signed link
    files_path="/api/files",
    openapi_url=f"{REST}/openapi.json",        # FastAPI's own arguments
    docs_url=f"{REST}/docs",
)                                              # /healthz and /readyz stay at the root
```

MCP, in a process of its own — stateless, so its replicas need no affinity ([mcp.md](mcp.md)):

```python
from sincpro_framework.entrypoints import McpGateway

app = McpGateway([billing, sales]).server(auth=access).http_app(
    path="/api/mcp", stateless_http=True
)
```

The ingress, routing by path as Sincpro's Odoo template routes `/websocket` and `/` (Kong,
`strip-path: false`, so each process sees the path it serves; the longest prefix wins):

```yaml
rules:
  - host: acme.sincpro.com.bo
    http:
      paths:
        - { path: /api/mcp, pathType: Prefix, backend: { service: { name: acme-mcp, port: { number: 8000 } } } }
        - { path: /api, pathType: Prefix, backend: { service: { name: acme-api, port: { number: 8000 } } } }
        # only when the MCP publishes OAuth metadata (RFC 9728 puts it at the root)
        - { path: /.well-known/oauth-protected-resource, pathType: Prefix, backend: { service: { name: acme-mcp, port: { number: 8000 } } } }
        - { path: /, pathType: Prefix, backend: { service: { name: acme-web, port: { number: 3000 } } } }
```

## The web beside it

The web is a client like any other, on the same host:

- In the browser it calls `/api/rest/v1/…` on its own origin — no CORS, no URL per environment.
- A server that renders pages (Next.js) calls the service by the cluster's address
  (`http://acme-api:8000/api/rest/v1`), never by the public host: no trip out and back in, no
  TLS to the ingress, and the trace goes straight from the render to the use case.
- Several applications of one product (a public site, a customer portal, an administration) are
  paths of the one web (`/`, `/portal`, `/admin`), not hosts — until one needs a trust boundary
  of its own, as above. How each is built is web-framework's.
