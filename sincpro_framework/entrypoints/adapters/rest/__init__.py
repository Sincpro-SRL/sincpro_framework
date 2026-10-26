"""REST wire: use cases as HTTP resources with an OpenAPI 3.1 document — Commands on POST,
`Query`s on GET — what `RestGateway` builds on. Starlette is needed only to serve (`[rest]`
extra); the route table and the document are built without it.

    build_routes(gateway.operations(), "/api/v1", {})    # the route table — no web library
    openapi_document(title, version, routes)             # the document
    serving.routes(...)                                  # served on Starlette
"""

from sincpro_framework.entrypoints.adapters.rest.routing import RestRoute
from sincpro_framework.entrypoints.adapters.rest.serving import REST_MISSING

__all__ = ["REST_MISSING", "RestRoute"]
