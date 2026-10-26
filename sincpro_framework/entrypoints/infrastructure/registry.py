"""The registry: every binding, by the handler class it was declared on — never written onto the
class — the latest class declared under each name, which is how a replaced handler named by
`module.Class` is found, and the classes marked `internal`.

Context: the same shape as `AccessControl`'s declarations. Weak on both sides, so a use case
loaded again lets its old class go. A `replaces=` handler inherits, wire by wire, the binding of
the one it replaces when it declares none for that wire — replacing a handler never moves or
drops a public operation; the composition's `exclude` does that on purpose.
"""

from collections.abc import Iterable, Mapping
from weakref import WeakKeyDictionary, WeakSet, WeakValueDictionary

from sincpro_framework import exceptions
from sincpro_framework.entrypoints.domain.bindings import Binding


class Bindings:
    """Every exposure declared in the process — the decorators write it, the gateways read it.

        entrypoint_bindings.declare(
            IssueInvoice, RestBinding(method="POST", path="/invoices")
        )
        entrypoint_bindings.of(IssueInvoice)  ->  {"rest": RestBinding(method="POST", …)}
        # inherits the replaced one's
        entrypoint_bindings.of(NewIssue, ["billing.IssueInvoice"])

    Context: stored apart from the class, so a decorator never adds an attribute a handler could
    shadow or a subclass inherit by accident.
    """

    def __init__(self) -> None:
        """Empty, every map weak — a class let go takes its bindings and its mark with it."""
        self._by_class: WeakKeyDictionary[type, dict[str, Binding]] = WeakKeyDictionary()
        self._by_name: WeakValueDictionary[str, type] = WeakValueDictionary()
        self._internal: WeakSet[type] = WeakSet()

    def declare[T: type](self, cls: T, binding: Binding) -> T:
        """Store `binding` for `cls` and remember `cls` as the newest class under its dotted
        name — `cls` returned, so a decorator returns it as it is.

        Context: one binding per wire — a second one for the same wire is refused, since
        only one of them could be published.

            entrypoint_bindings.declare(
            IssueInvoice, RestBinding(method="POST", path="/invoices")
        )
            ->  IssueInvoice
            entrypoint_bindings.declare(IssueInvoice, RestBinding(path="/v2/invoices"))
            ->  ProgrammingError: IssueInvoice declares two rest bindings — ...

        Bindings of other wires on the same class are stored side by side.
        """
        held = self._by_class.setdefault(cls, {})
        if binding.wire in held:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} declares two {binding.wire} bindings — {held[binding.wire]!r} "
                f"and {binding!r}: declare one"
            )
        held[binding.wire] = binding
        self._by_name[f"{cls.__module__}.{cls.__qualname__}"] = cls
        return cls

    def of(self, cls: type, replaced: Iterable[str] = ()) -> Mapping[str, Binding]:
        """Every binding `cls` answers with, by wire — its own, and for a wire it declares
        nothing on, the newest of the handlers it replaced that did (`replaced` oldest first).

            entrypoint_bindings.of(NewIssue, ["billing.features.IssueInvoice"])
            ->  {"rest": <IssueInvoice's>, "mcp": <NewIssue's own>}   # NewIssue declares mcp
            entrypoint_bindings.of(Undeclared)                                      ->  {}

        A replaced class that was let go (weakly held) is skipped.
        """
        found: dict[str, Binding] = {}
        for name in reversed(tuple(replaced)):
            older = self._by_name.get(name)
            if older is None:
                continue
            for wire, binding in self._by_class.get(older, {}).items():
                found.setdefault(wire, binding)
        return {**found, **self._by_class.get(cls, {})}

    def declared(self) -> list[type]:
        """Every class still alive that declared a binding — the newest under each name."""
        return list(self._by_name.values())

    def mark_internal[T: type](self, cls: T) -> T:
        """Record `cls` — a Feature, ApplicationService or Command — as on no wire; `cls`
        returned, so the `internal` decorator returns it as it is."""
        self._internal.add(cls)
        return cls

    def is_internal(self, *classes: type | None) -> bool:
        """Whether any of `classes` — the Command, its handler — was marked `internal`; a `None`
        (no handler found) counts as not marked.

            entrypoint_bindings.is_internal(CommandReconcileLedger, ReconcileLedger)  ->  True
            entrypoint_bindings.is_internal(CommandIssueInvoice, None)  ->  False
        """
        return any(one is not None and one in self._internal for one in classes)


entrypoint_bindings = Bindings()
"""The process's one registry — every decorator writes here, every gateway reads it."""
