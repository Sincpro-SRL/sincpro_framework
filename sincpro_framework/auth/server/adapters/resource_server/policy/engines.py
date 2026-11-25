"""Policy engines over HTTP — the standard library, no SDK.

    AuthZenEngine("https://pdp.internal")                         any OpenID AuthZEN PDP
    OpaEngine("https://opa.internal", path="billing/allow")        Open Policy Agent's data API
    OpenFgaEngine("https://fga.internal", store_id="01H…",         OpenFGA: check, batch-check,
                  types={Invoice: "invoice"}, scope_relation="viewer")    list-objects → a scope

Context: a decision is the engine's own, asked each time — none is cached here: a policy that
changes holds at once. `headers` authenticates this service to the engine — a dict, or a function
answering one (`lambda: token_source.headers_for(request)`).
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpClient
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.server.adapters.resource_server.policy.requests import (
    DescribeResource,
    post_json,
    resource_of,
    subject_of,
)
from sincpro_framework.auth.server.domain.resource_server.policy import PolicyEngine
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator

type Headers = Mapping[str, str] | Callable[[], Mapping[str, str]] | None


class AuthZenEngine(PolicyEngine):
    """An OpenID AuthZEN Policy Decision Point: `/access/v1/evaluation` and `/evaluations`."""

    def __init__(
        self,
        base_url: str,
        http: HttpClient | None = None,
        headers: Headers = None,
        describe_resource: DescribeResource = resource_of,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http: HttpClient = http or UrllibHttp()
        self.headers = headers
        self.describe_resource = describe_resource

    def decide(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """One AuthZEN evaluation — allowed only when the PDP's `decision` is `true`."""
        response = post_json(
            self.http,
            f"{self.base_url}/access/v1/evaluation",
            {
                "subject": subject_of(identity),
                "action": {"name": permission},
                "resource": self.describe_resource(resource, permission),
                "context": dict(context or {}),
            },
            self.headers,
        )
        return response.get("decision") is True

    def decide_many(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """One AuthZEN `evaluations` request — a decision per resource, in their order."""
        response = post_json(
            self.http,
            f"{self.base_url}/access/v1/evaluations",
            {
                "subject": subject_of(identity),
                "action": {"name": permission},
                "context": dict(context or {}),
                "evaluations": [
                    {"resource": self.describe_resource(one, permission)} for one in resources
                ],
            },
            self.headers,
        )
        decisions = response.get("evaluations", [])
        return [
            isinstance(one, dict) and one.get("decision") is True for one in decisions
        ] + [False] * (len(resources) - len(decisions))


class OpaEngine(PolicyEngine):
    """Open Policy Agent: `POST /v1/data/<path>` with the decision's input; `result: true` allows,
    anything else — an undefined rule included — refuses."""

    def __init__(
        self,
        base_url: str,
        path: str,
        http: HttpClient | None = None,
        headers: Headers = None,
        describe_resource: DescribeResource = resource_of,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.path = path.strip("/")
        self.http: HttpClient = http or UrllibHttp()
        self.headers = headers
        self.describe_resource = describe_resource

    def decide(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """OPA's `/v1/data/<path>` asked with the input — allowed only on a `result` that is
        `true`; an undefined one refuses."""
        response = post_json(
            self.http,
            f"{self.base_url}/v1/data/{self.path}",
            {
                "input": {
                    "subject": subject_of(identity),
                    "action": permission,
                    "resource": self.describe_resource(resource, permission),
                    "context": dict(context or {}),
                }
            },
            self.headers,
        )
        return response.get("result") is True


class OpenFgaEngine(PolicyEngine):
    """OpenFGA: a permission is a relation of the subject to the resource (`check`), many at once
    (`batch-check`), and the records it may read are the objects it relates to (`list-objects`).
    """

    def __init__(
        self,
        base_url: str,
        store_id: str,
        model_id: str | None = None,
        relations: Mapping[str, str] | None = None,
        types: Mapping[type, str] | None = None,
        scope_relation: str = "viewer",
        max_objects: int = 1000,
        http: HttpClient | None = None,
        headers: Headers = None,
        describe_resource: DescribeResource = resource_of,
    ) -> None:
        """`relations` names the relation each permission is — the permission itself when not
        named; `types` names the OpenFGA type of an aggregate, for `scope`."""
        self.base_url = f"{base_url.rstrip('/')}/stores/{store_id}"
        self.model_id = model_id
        self.relations = dict(relations or {})
        self.types = dict(types or {})
        self.scope_relation = scope_relation
        self.max_objects = max_objects
        self.http: HttpClient = http or UrllibHttp()
        self.headers = headers
        self.describe_resource = describe_resource

    def _tuple(self, identity: Identity, permission: str, resource: Any) -> dict[str, str]:
        described = self.describe_resource(resource, permission)
        return {
            "user": identity.subject,
            "relation": self.relations.get(permission, permission),
            "object": f"{described['type']}:{described['id']}",
        }

    def _model(self) -> dict[str, str]:
        return {} if self.model_id is None else {"authorization_model_id": self.model_id}

    def decide(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """One OpenFGA `check` of the permission's relation on the resource."""
        response = post_json(
            self.http,
            f"{self.base_url}/check",
            {
                **self._model(),
                "tuple_key": self._tuple(identity, permission, resource),
                "context": dict(context or {}),
            },
            self.headers,
        )
        return response.get("allowed") is True

    def decide_many(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """One OpenFGA `batch-check`, a correlation id per resource — answered in their order."""
        response = post_json(
            self.http,
            f"{self.base_url}/batch-check",
            {
                **self._model(),
                "checks": [
                    {
                        "tuple_key": self._tuple(identity, permission, one),
                        "correlation_id": str(index),
                        "context": dict(context or {}),
                    }
                    for index, one in enumerate(resources)
                ],
            },
            self.headers,
        )
        result = response.get("result", {})
        return [
            isinstance(result.get(str(index)), dict)
            and result[str(index)].get("allowed") is True
            for index in range(len(resources))
        ]

    def scope(self, identity: Identity, aggregate: type) -> Criteria | None:
        """The records whose ids OpenFGA lists for the identity's `scope_relation` — `None` for an
        aggregate `types` does not name. More than `max_objects`: `ProgrammingError` — a list
        that long is a filter the store should hold, not a query."""
        kind = self.types.get(aggregate)
        if kind is None:
            return None
        response = post_json(
            self.http,
            f"{self.base_url}/list-objects",
            {
                **self._model(),
                "user": identity.subject,
                "relation": self.scope_relation,
                "type": kind,
            },
            self.headers,
        )
        objects = [one.split(":", 1)[1] for one in response.get("objects", []) if ":" in one]
        if len(objects) > self.max_objects:
            raise exceptions.ProgrammingError(
                f"OpenFGA lists {len(objects)} {kind}s, more than {self.max_objects}"
            )
        return Criteria(selection=Condition(field="id", value=objects, operator=Operator.IN))
