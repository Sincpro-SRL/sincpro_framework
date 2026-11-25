"""Which `AccessControl` guards each bus of the process — and each generation `fresh()` makes of
it.

    guarded_buses[billing]   ->  the AccessControl `auth.on(billing)` put there

Context: what an entrypoint asks to authenticate a call, and where an identity vouched for by
another bounded context finds its provider. Weak: a bus that goes away takes its entry with it.
"""

from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from sincpro_framework.auth.access_control.domain.access import Access

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

guarded_buses: "WeakKeyDictionary[UseFramework, Access]" = WeakKeyDictionary()
"""The `AccessControl` guarding each bus of the process, by the bus."""


def find_guarding(context: str) -> list[Access]:
    """Every `AccessControl` of this process, those guarding a bus of `context` first —
    what a call to the service hosting `context` asks for the credentials it sends.

        # auth_b guards the "billing" bus, auth_s the "sales" bus and its generations
        find_guarding("sales")     ->  [auth_s, auth_b]
        find_guarding("payroll")   ->  [auth_b, auth_s]    # as registered

    Context: an `AccessControl` guarding several buses — the generations `fresh()` makes —
    is listed once, where it first appears.
    """
    found: list[Access] = []
    for _, access in sorted(
        list(guarded_buses.items()), key=lambda item: item[0].name != context
    ):
        if access not in found:
            found.append(access)
    return found
