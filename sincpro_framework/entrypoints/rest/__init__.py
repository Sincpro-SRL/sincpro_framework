"""REST wire: the buses' use cases as HTTP resources with an OpenAPI 3.1 document — Commands on
POST, `Query`s on GET. Starlette is needed only to serve (`[rest]` extra); the route table and
the document are built without it."""

from sincpro_framework.entrypoints.rest.entrypoint import (
    REST_MISSING,
    RestGateway,
    build_rest_app,
)
from sincpro_framework.entrypoints.rest.routing import RestRoute, kebab

__all__ = ["REST_MISSING", "RestGateway", "RestRoute", "build_rest_app", "kebab"]
