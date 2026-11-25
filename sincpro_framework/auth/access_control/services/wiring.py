"""How an `AccessControl` is wired onto what it guards: the guard on a bus and on every generation
`fresh()` makes of it, itself as the handlers' `auth`, the gate of a `Hooks` collection — and one
`AccessControl` put in the place of another (`on(bus, replaces=...)`).

    wiring = Wiring(auth)
    wiring.guard(billing)                   # what auth.on(billing) does
    wiring.guard(test_bus, replaced=auth)   # what other.on(test_bus, replaces=auth) does

Context: a bus is wired through an extension (`bus.extend`), so a generation `fresh()` makes runs
the same wiring against itself; the registry of who guards each bus (`guarded_buses`) is where
an entrypoint and another bounded context find the one guarding it.
"""

import functools
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet

from sincpro_framework.auth.access_control.domain.access import Access
from sincpro_framework.auth.access_control.services.authentication import (
    find_clashing_names,
)
from sincpro_framework.auth.access_control.services.guard import (
    GUARD_SEQUENCE,
    BusGuard,
    HookGuard,
)
from sincpro_framework.auth.access_control.services.guarded_buses import guarded_buses
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.permissions import Rule
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

_wirings: "WeakKeyDictionary[Any, Wiring]" = WeakKeyDictionary()
"""The wiring of each `AccessControl` of the process — what a replacement finds the guard of the
one it replaces by."""


def rewire_checks(
    checks: Sequence[Rule], replaced: Rule | None, replacement: Rule | None
) -> list[Rule]:
    """A copy of `checks` with `replaced` swapped for `replacement` — matched by identity, never
    by name, so a check of the project's that happens to share its name stays as it is."""
    return [
        replacement if replacement is not None and one is replaced else one for one in checks
    ]


def _identity_for_logs() -> dict[str, str]:
    """The `identity` field every log line of a guarded bus carries — none for nobody."""
    identity = current_identity()
    return {} if identity.is_anonymous else {"identity": identity.subject}


class Wiring:
    """What one `AccessControl` guards, and how it got there."""

    def __init__(self, access: Access) -> None:
        """Nothing guarded yet: no bus, no generation, no `Hooks`."""
        self.access = access
        self.buses: WeakSet["UseFramework"] = WeakSet()
        """Every bus guarded — those given to `on` and the generations made of them."""
        self.guards: "WeakKeyDictionary[UseFramework, BusGuard]" = WeakKeyDictionary()
        """The guard put on each bus — what `on(bus, replaces=this)` replaces."""
        self.roots: list["UseFramework"] = []
        self.hooks: list[Hooks] = []
        self.hook_guard = HookGuard(access)
        self._warned_names: set[str] = set()
        """The provider names a clash was already warned about."""
        _wirings[access] = self

    def attach(self, bus: "UseFramework") -> None:
        """What `guard(bus)` wires, and what every generation `fresh()` makes of the bus wires
        again against itself: the guard, and the `AccessControl` as the handlers' `auth` — and,
        once per name, a warning when another `AccessControl` of the process gives a provider of
        another kind the name of one of these."""
        for name in sorted(find_clashing_names(self.access.providers) - self._warned_names):
            self._warned_names.add(name)
            logger.warning(
                "two AccessControls of the process hold providers of different kinds under one "
                "name: an identity vouched for by one may be asked of the other — name them "
                "apart",
                provider=name,
            )
        guard = BusGuard(self.access, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE)(guard)
        bus.add_dependency("auth", self.access)
        self.guards[bus] = guard
        self.buses.add(bus)
        guarded_buses[bus] = self.access

    def replace(self, replaced: Access, bus: "UseFramework") -> None:
        """What `guard(bus, replaced)` wires, and every generation `fresh()` makes of the bus
        wires again: this guard in the place of the one guarding the bus, and this
        `AccessControl` as the handlers' `auth`.

        1. `replaced` guards the bus: its guard and its `auth` are replaced.
        2. Another `AccessControl` guards it: that one is replaced, with a warning.
        3. Nothing guards it: guarded as `guard(bus)` guards, with a warning.
        Final: the one replaced guards the bus no more.
        """
        own = _wirings.get(replaced)
        guarding = own if own is not None and bus in own.guards else None
        if guarding is None:
            found = guarded_buses.get(bus)
            guarding = None if found is None else _wirings.get(found)
        if guarding is None or bus not in guarding.guards:
            logger.warning(
                "replaces= names an AccessControl and nothing guards the bus: guarded as on() "
                "guards it",
                bus=bus.name,
            )
            self.attach(bus)
            return
        if guarding.access is not replaced:
            logger.warning(
                "replaces= names an AccessControl that does not guard the bus: the one "
                "guarding it is replaced",
                bus=bus.name,
            )
        guard = BusGuard(self.access, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE, replaces=guarding.guards[bus])(guard)
        bus.add_dependency("auth", self.access, replaces=guarding.access)
        self.guards[bus] = guard
        self.buses.add(bus)
        guarding.buses.discard(bus)
        guarded_buses[bus] = self.access

    def guard(self, target: "UseFramework | Hooks", replaced: Access | None = None) -> None:
        """Guard every use case of a bus — the bus not built yet — and inject the
        `AccessControl` in its handlers as `auth`; or every hook of a collection, and of every
        copy made of it.

        1. Disabled: a warning is logged — the target is wired anyway, and checks nothing.
        2. `replaced` given: a `Hooks` collection is refused — one `AccessControl` gates it;
           a bus has `replace` registered as its extension.
        3. A `Hooks` collection:
           3.1 one already guarded: a warning, guarded once;
           3.2 else the hook guard is set as its gate and the collection stored.
        4. A bus already guarded: a warning, guarded once.
        5. `attach` is registered as an extension of the bus — run on it and on every
           generation made of it, so the guard and `auth` reach each.
        6. Each log line of the bus gets the `identity` of who acts.
        Final: the bus is stored among the roots guarded.
        """
        if not self.access.enabled:
            logger.warning("access control is disabled: nothing it guards is checked")
        if replaced is not None:
            if isinstance(target, Hooks):
                raise exceptions.ProgrammingError(
                    "replaces= is for a bus: a Hooks collection is gated by one AccessControl"
                )
            target.extend(functools.partial(self.replace, replaced))
            self.roots.append(target)
            return
        if isinstance(target, Hooks):
            if any(target is one for one in self.hooks):
                logger.warning("AccessControl already guards this Hooks: guarded once")
                return
            target.gate(self.hook_guard)
            self.hooks.append(target)
            return
        if target in self.roots:
            logger.warning(
                "AccessControl already guards the bus: guarded once", bus=target.name
            )
            return
        target.extend(self.attach)
        target.logger.add_context_source(_identity_for_logs)
        self.roots.append(target)

    def find_refusal(
        self, bus: "UseFramework", command: type, identity: Identity | None, dto: Any
    ) -> exceptions.AuthError | None:
        """Why `identity` — the one in play when none is given — may not run `command` on
        `bus`, without running it: the very decision the bus's guard takes. `None` when it may.

        Context: a bus another `AccessControl` guards is answered by that one, and a bus nobody
        guards runs anything — `None`.
        """
        who = identity or current_identity()
        guard = self.guards.get(bus)
        if guard is not None:
            return guard.find_refusal(command, who, dto)
        found = guarded_buses.get(bus)
        other = None if found is None or found is self.access else _wirings.get(found)
        return None if other is None else other.find_refusal(bus, command, who, dto)
