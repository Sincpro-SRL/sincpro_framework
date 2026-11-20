"""Where assignments and entitlements live: in this process (`InMemoryGrants`), or on a
`KeyValueStore` every replica shares (`KeyValueGrants`). A table of the project's is
`DatabaseGrants`, in `data_layer.orm`.

    grants = KeyValueGrants()                         the running context's store
    grants.assign(Assignment(subject="user:ana", tenant="bo", role="billing.manager"))
    grants.held("user:ana", "bo")                     ->  Held(roles={"billing.manager"}, …)

Context: a subject's assignments in a tenant are one record, written by compare-and-set — two
administrators assigning at once never lose one another's role. An index of holders per role
answers "who holds it"; it names subjects, which an administration screen needs.
"""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sincpro_framework.auth.domain.grants import Assignment, GrantStore
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.exceptions import ServiceUnavailableError

ATTEMPTS = 64


class InMemoryGrants(GrantStore):
    """Assignments and entitlements in this process — a test, a CLI, one replica."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        self._now = now
        self._assigned: dict[tuple[str, str | None], dict[str, Assignment]] = {}
        self._entitled: dict[str | None, set[str]] = {}
        self._lock = threading.Lock()

    def now(self) -> datetime:
        return self._now()

    def assign(self, assignment: Assignment) -> None:
        with self._lock:
            key = (assignment.subject, assignment.tenant)
            self._assigned.setdefault(key, {})[assignment.role] = assignment

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        with self._lock:
            self._assigned.get((subject, tenant), {}).pop(role, None)

    def assignments_of(self, subject: str, tenant: str | None) -> list[Assignment]:
        with self._lock:
            return sorted(
                self._assigned.get((subject, tenant), {}).values(), key=lambda one: one.role
            )

    def holders(self, role: str, tenant: str | None) -> list[Assignment]:
        with self._lock:
            return sorted(
                (
                    given[role]
                    for (_, of_tenant), given in self._assigned.items()
                    if of_tenant == tenant and role in given
                ),
                key=lambda one: one.subject,
            )

    def entitle(self, tenant: str, entitlement: str) -> None:
        with self._lock:
            self._entitled.setdefault(tenant, set()).add(entitlement)

    def withdraw(self, tenant: str, entitlement: str) -> None:
        with self._lock:
            self._entitled.get(tenant, set()).discard(entitlement)

    def entitlements_of(self, tenant: str | None) -> frozenset[str]:
        with self._lock:
            return frozenset(self._entitled.get(tenant, set()))


class KeyValueGrants(GrantStore):
    """Assignments and entitlements on a `KeyValueStore`, under `prefix`."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:grants",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` pins one instead of the running context's."""
        self._store = store
        self.prefix = prefix
        self._now = now

    def now(self) -> datetime:
        return self._now()

    def store(self) -> KeyValueStore:
        """The store given, else the running context's — read at every call."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _assigned_key(self, subject: str, tenant: str | None) -> str:
        return f"{self.prefix}:assigned:{digest(subject)}:{digest(tenant or '')}"

    def _holders_key(self, role: str, tenant: str | None) -> str:
        return f"{self.prefix}:holders:{digest(tenant or '')}:{role}"

    def _entitled_key(self, tenant: str | None) -> str:
        return f"{self.prefix}:entitled:{digest(tenant or '')}"

    def _read(self, key: str) -> list[dict[str, Any]]:
        """The records under `key` — an empty list for nothing."""
        raw = self.store().get(key)
        return [] if raw is None else json.loads(raw)

    def _update(
        self, key: str, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    ) -> None:
        """`change` applied to the records under `key`, written by compare-and-set — tried
        again when another writer won, `ServiceUnavailableError` past `ATTEMPTS`."""
        store = self.store()
        for _ in range(ATTEMPTS):
            current = store.get(key)
            records = [] if current is None else json.loads(current)
            written = json.dumps(change(records), sort_keys=True).encode()
            if current is None and store.add(key, written):
                return
            if current is not None and store.replace(key, current, written):
                return
        raise ServiceUnavailableError(f"grants under {key} are contended: try again")

    def assign(self, assignment: Assignment) -> None:
        record = assignment.model_dump(mode="json")
        self._update(
            self._assigned_key(assignment.subject, assignment.tenant),
            lambda records: [one for one in records if one["role"] != assignment.role]
            + [record],
        )
        self._update(
            self._holders_key(assignment.role, assignment.tenant),
            lambda records: [one for one in records if one["subject"] != assignment.subject]
            + [record],
        )

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        self._update(
            self._assigned_key(subject, tenant),
            lambda records: [one for one in records if one["role"] != role],
        )
        self._update(
            self._holders_key(role, tenant),
            lambda records: [one for one in records if one["subject"] != subject],
        )

    def assignments_of(self, subject: str, tenant: str | None) -> list[Assignment]:
        found = [
            Assignment.model_validate(one)
            for one in self._read(self._assigned_key(subject, tenant))
        ]
        return sorted(found, key=lambda one: one.role)

    def holders(self, role: str, tenant: str | None) -> list[Assignment]:
        found = [
            Assignment.model_validate(one)
            for one in self._read(self._holders_key(role, tenant))
        ]
        return sorted(found, key=lambda one: one.subject)

    def entitle(self, tenant: str, entitlement: str) -> None:
        self._update(
            self._entitled_key(tenant),
            lambda records: [one for one in records if one["key"] != entitlement]
            + [{"key": entitlement}],
        )

    def withdraw(self, tenant: str, entitlement: str) -> None:
        self._update(
            self._entitled_key(tenant),
            lambda records: [one for one in records if one["key"] != entitlement],
        )

    def entitlements_of(self, tenant: str | None) -> frozenset[str]:
        return frozenset(one["key"] for one in self._read(self._entitled_key(tenant)))
