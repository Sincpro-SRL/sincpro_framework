"""`self.context` in a Feature or an ApplicationService — the bus's `Context`, read where the
handler runs."""

from typing import Any


class ContextConsumer:
    """`self.context`: the `Context` of the execution in play, as its bus sees it — read and written
    like a mapping, navigated like `use_context()` (`self.context.entrypoint`, `.parent`).

    Autocomplete of ``self.context`` is declared on the Feature stub as ``ContextT``, not here. This
    class only binds the bus once it is built.
    """

    _context_binder: Any
    _context_fallback: dict

    def bind_to_framework(self, binder: Any) -> None:
        """Bind the handler to the bus it reads its context from — done once per bus
        generation, when the bus is built."""
        self._context_binder = binder

    @property
    def context(self) -> Any:
        """The bus's `Context` where the handler runs — a plain dict when no bus is bound yet
        (a handler built by hand, in a test)."""
        binder = self._context_binder
        if binder is not None:
            return binder._get_context()
        return self._context_fallback

    @context.setter
    def context(self, value: Any) -> None:
        """`self.context = {...}` replaces what the handler's scope says — on the bus's
        context when bound, on the fallback dict otherwise; `None` clears it.

            self.context = {"company": 3}   ->  self.context["company"]  ->  3
            self.context = None             ->  the handler's scope says nothing of its own

        The value is copied into a new dict first; a parent's keys are not touched."""
        mapping = dict(value) if value is not None else {}
        binder = self._context_binder
        if binder is not None:
            binder._set_context(mapping)
            return
        self._context_fallback = mapping
