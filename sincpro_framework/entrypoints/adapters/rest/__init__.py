"""REST wire: the buses' use cases as HTTP resources with an OpenAPI 3.1 document — Commands on
POST, `Query`s on GET. Starlette is needed only to serve (`[rest]` extra); the route table and
the document are built without it.

    rest = RestGateway([billing], prefix="/api/v1")
    rest.rest_routes()      # the route table — no web library
    rest.openapi()          # the document
    uvicorn.run(rest.app()) # served, with /openapi.json, /docs and /healthz

    kebab("CommandIssueInvoice")  ->  "command-issue-invoice"
"""

from sincpro_framework.entrypoints.adapters.rest.entrypoint import (
    REST_MISSING,
    RestGateway,
    build_rest_app,
)
from sincpro_framework.entrypoints.adapters.rest.routing import RestRoute, kebab

__all__ = ["REST_MISSING", "RestGateway", "RestRoute", "build_rest_app", "kebab"]
