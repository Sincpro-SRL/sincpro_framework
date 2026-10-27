"""`BusApprovalsMixin`: what a `UseFramework` mixes in so each use case that declares an approval
asks for one before it runs.

When the bus is built, an interceptor — `ApprovalGate` — is attached to the Command of every
Feature and ApplicationService that declares `approval = Approval()`, and the `Approvals` of the
bus is handed those gates and the bus to run what is approved on. Nothing else is declared.
"""

from typing import TYPE_CHECKING

from sincpro_framework.approvals.domain.approval import Approval
from sincpro_framework.approvals.entrypoint.approvals import Approvals
from sincpro_framework.approvals.services.gate import ApprovalGate
from sincpro_framework.bus_pipeline.interceptors.interceptor import InterceptorRegistration
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import Owner
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus


class BusApprovalsMixin:
    """What a `UseFramework` adds to guard the use cases that need approval."""

    if TYPE_CHECKING:

        @property
        def name(self) -> str:
            """The bus's name — declared for the type checker; `UseFramework` defines it."""
            ...

    def _approval_gates(self, bus: "FrameworkBus") -> list[InterceptorRegistration]:
        """The gates of the use cases of `bus` that declare an approval — one interceptor each.

        1. The `Approvals` of the bus: whose approvals are is what it says (`owned_by`), the
           tenant's and the user's when there is none.
        2. A gate for each handler whose class declares `approval = Approval()`.
        3. Approvals declared and nothing to decide them is said in the logs, never refused.
        Final: each `Approvals` handed the gates and this bus; the interceptors, one per gate.

        Context: read at every build, so each generation of the bus guards its own handlers.
        """
        handlers = {
            **bus.feature_bus.feature_registry,
            **bus.app_service_bus.app_service_registry,
        }
        deciders = [one for one in handlers.values() if isinstance(one, Approvals)]
        owned_by = tuple(deciders[0].owned_by) if deciders else Owner.BY_TENANT_AND_USER
        gates: dict[str, ApprovalGate] = {}
        for command, handler in handlers.items():
            declared = getattr(type(handler), "approval", None)
            if isinstance(declared, Approval):
                gate = ApprovalGate(handler, command, declared, owned_by, self.name)
                gates[registered_name(command, self.name)] = gate
        if gates and not deciders:
            logger.warning(
                f"{', '.join(gates)} declare an approval and nothing on '{self.name}' decides "
                "it: register @bus.feature([Approve, Reject, GetApproval, FindApprovals]) on a "
                "class of Approvals"
            )
        for decider in deciders:
            decider.gates = gates
            decider.decides_on = self
        return [InterceptorRegistration(gate, (gate.command,)) for gate in gates.values()]
