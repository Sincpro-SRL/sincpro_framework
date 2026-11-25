"""Where assignments and entitlements live: on a `KeyValueStore` every replica shares
(`KeyValueGrants`), or in this process (`InMemoryGrants`, the same over an `InMemoryKeyValue`). A
table of the project's is `DatabaseGrants`, in `data_layer.orm`.

    grants = KeyValueGrants()                         the running context's store
    grants.assign(Assignment(subject="user:ana", tenant="bo", role="billing.manager"))
    grants.grants_of("user:ana", "bo")                ->  SubjectGrants(roles={"billing.manager"}, …)

Context: a subject's assignments in a tenant are one record, written by compare-and-set — two
administrators assigning at once never lose one another's role. An index of holders per role
answers "who holds it"; it names subjects, which an administration screen needs. The record and
the index are two writes, each atomic, not one: a store failing between them leaves the index
behind the record — the role assigned and in force, missing from "who holds it" — until the same
assignment, or any other change to that role's holders, is written again. Every rewrite of the
index drops the temporary assignments that ended, so it never grows with every one ever given.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore


class KeyValueGrants(KeyValueStoreMixin, GrantStore):
    """Assignments and entitlements on a `KeyValueStore`, under `prefix`."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:grants",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` pins one instead of the running context's."""
        self._init_key_value_store(store, prefix)
        self._now = now

    def now(self) -> datetime:
        """The moment an assignment's `valid_until` is judged against."""
        return self._now()

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

    def _update_records(
        self, key: str, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    ) -> None:
        """`change` applied to the records under `key`, written by compare-and-set
        (`KeyValueStoreMixin.update`) — stored with no expiry."""

        def changed(raw: bytes | None) -> tuple[bytes, timedelta | None]:
            records = [] if raw is None else json.loads(raw)
            return json.dumps(change(records), sort_keys=True).encode(), None

        self.update(key, changed)

    def _update_holders(
        self,
        role: str,
        tenant: str | None,
        change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    ) -> None:
        """`change` applied to the role's holders, the temporary assignments that ended
        dropped."""
        now = self.now()
        self._update_records(
            self._holders_key(role, tenant),
            lambda records: change(
                [one for one in records if Assignment.model_validate(one).is_active_at(now)]
            ),
        )

    def assign(self, assignment: Assignment) -> None:
        """Written twice, each by compare-and-set: under the subject's assignments and under
        the role's holders — so both are read in one `get`. The second write failing leaves the
        index behind until the assignment is written again."""
        record = assignment.model_dump(mode="json")
        self._update_records(
            self._assigned_key(assignment.subject, assignment.tenant),
            lambda records: [one for one in records if one["role"] != assignment.role]
            + [record],
        )
        self._update_holders(
            assignment.role,
            assignment.tenant,
            lambda records: [one for one in records if one["subject"] != assignment.subject]
            + [record],
        )

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        """Dropped from the subject's assignments and from the role's holders, each by
        compare-and-set."""
        self._update_records(
            self._assigned_key(subject, tenant),
            lambda records: [one for one in records if one["role"] != role],
        )
        self._update_holders(
            role,
            tenant,
            lambda records: [one for one in records if one["subject"] != subject],
        )

    def assignments_of(self, subject: str, tenant: str | None) -> list[Assignment]:
        """The subject's assignments in `tenant`, in one `get`, ordered by role."""
        found = [
            Assignment.model_validate(one)
            for one in self._read(self._assigned_key(subject, tenant))
        ]
        return sorted(found, key=lambda one: one.role)

    def holders(self, role: str, tenant: str | None) -> list[Assignment]:
        """The role's holders in `tenant`, in one `get`, ordered by subject — a temporary
        assignment that ended until the index is next rewritten."""
        found = [
            Assignment.model_validate(one)
            for one in self._read(self._holders_key(role, tenant))
        ]
        return sorted(found, key=lambda one: one.subject)

    def entitle(self, tenant: str, entitlement: str) -> None:
        """Added to the tenant's entitlements by compare-and-set — again is the same."""
        self._update_records(
            self._entitled_key(tenant),
            lambda records: [one for one in records if one["key"] != entitlement]
            + [{"key": entitlement}],
        )

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """Removed from the tenant's entitlements by compare-and-set."""
        self._update_records(
            self._entitled_key(tenant),
            lambda records: [one for one in records if one["key"] != entitlement],
        )

    def entitlements_of(self, tenant: str | None) -> frozenset[str]:
        """The tenant's entitlements, in one `get`."""
        return frozenset(one["key"] for one in self._read(self._entitled_key(tenant)))


class InMemoryGrants(KeyValueGrants):
    """Assignments and entitlements in this process — a test, a CLI, one replica."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        super().__init__(InMemoryKeyValue(now=now), now=now)
