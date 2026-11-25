"""Requirements an administrator adds to a use case at runtime, beside what its code declares —
for every tenant (the platform's) or for one tenant (that tenant's): every permission named and,
when roles are named, one of them.

    AddedRequirement(command="billing.services.approve.CommandApprovePayment",
                     permissions=frozenset({"billing.payment.approve"}))          every tenant
    AddedRequirement(command="...CommandApprovePayment", tenant="bo",
                     roles=frozenset({"cash.supervisor@bo"}))                      tenant bo only

Context: a use case is named by its Command's dotted name — what `describe()` lists — so two
bounded contexts with a Command of one name never share what is added to either. Each scope is
one versioned document, written over the version it was read at. They only ever add: what the
code declares holds whatever is added here.
"""

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain.documents import ScopedDocuments
from sincpro_framework.sincpro_abstractions import DataTransferObject


class AddedRequirement(DataTransferObject):
    """What a use case requires besides its code's declaration."""

    model_config = ConfigDict(frozen=True)

    command: str
    """The Command's dotted name — `module.CommandApprovePayment`, as `describe()` lists it."""
    tenant: str | None = None
    """The tenant it holds for — `None`: every tenant, the platform's."""
    permissions: frozenset[str] = frozenset()
    """Every one of them — each a permission the code declares."""
    roles: frozenset[str] = frozenset()
    """One of them, when any is named."""


class AddedRequirements(DataTransferObject):
    """The requirements added for one scope, and the version they were written under."""

    model_config = ConfigDict(frozen=True)

    version: int = 0
    requirements: tuple[AddedRequirement, ...] = ()

    def of(self, command: str) -> AddedRequirement | None:
        """What is added to `command` here — `None` when nothing is."""
        return next((one for one in self.requirements if one.command == command), None)

    def replacing(self, added: AddedRequirement) -> "AddedRequirements":
        """The next version, with `added` new or in place of what its Command had."""
        kept = tuple(one for one in self.requirements if one.command != added.command)
        return AddedRequirements(version=self.version + 1, requirements=(*kept, added))

    def without(self, command: str) -> "AddedRequirements":
        """The next version, with nothing added to `command`."""
        return AddedRequirements(
            version=self.version + 1,
            requirements=tuple(one for one in self.requirements if one.command != command),
        )


class AddedRequirementStore(ScopedDocuments[AddedRequirements]):
    """The port: the requirements added at runtime, one `AddedRequirements` per scope — `None`
    for every tenant, a tenant for its own."""
