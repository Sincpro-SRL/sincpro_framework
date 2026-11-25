"""What every engine of this folder shares: how a subject and a resource are described, and a JSON
POST to the engine whose failures are `EndpointUnavailable`."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpClient, HttpRequest
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.sincpro_logger import logger

type DescribeResource = Callable[[Any, str], dict[str, Any]]


def subject_of(identity: Identity) -> dict[str, Any]:
    """The subject as AuthZEN writes it: its kind, its id, and what may matter to a policy."""
    return {
        "type": identity.kind.value,
        "id": identity.subject,
        "properties": {
            "tenant": identity.tenant,
            "roles": sorted(identity.roles),
            "scopes": sorted(identity.scopes),
        },
    }


def resource_of(resource: Any, permission: str) -> dict[str, Any]:
    """A resource as `{type, id}` — its class and its `id`; with none, the permission's subject
    (`billing.invoice.issue` → `invoice`)."""
    if resource is None:
        parts = permission.split(".")
        return {"type": parts[-2] if len(parts) > 1 else permission, "id": "*"}
    return {
        "type": type(resource).__name__.lower(),
        "id": str(getattr(resource, "id", resource)),
    }


def posted(
    http: HttpClient,
    url: str,
    body: Mapping[str, Any],
    headers: Mapping[str, str] | Callable[[], Mapping[str, str]] | None,
) -> dict[str, Any]:
    """The JSON object `url` answers to `body` — `EndpointUnavailable` for anything else."""
    sent = dict(headers() if callable(headers) else (headers or {}))
    answer = http.send(
        HttpRequest(
            url=url,
            method="POST",
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                **sent,
            },
            body=json.dumps(body).encode(),
        )
    )
    target = HttpRequest(url=url).target
    if not answer.ok:
        if answer.status < 500:
            logger.error(
                "a policy engine refused this service's request",
                endpoint=target,
                status=answer.status,
            )
        raise exceptions.EndpointUnavailable(
            target, f"answered {answer.status}", answer.status, answer.retry_after()
        )
    said = answer.json_object()
    if not said:
        raise exceptions.EndpointUnavailable(target, "answered no JSON object")
    return said
