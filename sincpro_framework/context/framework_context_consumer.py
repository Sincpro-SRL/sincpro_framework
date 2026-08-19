from typing import Any


class ContextConsumer:
    """Runtime access to the UseFramework context dict from Feature / ApplicationService.

    Autocomplete of ``self.context`` is declared on the Feature stub as ``ContextT``,
    not here. This class only binds the live dict (overlay or shared) after the bus
    is built.
    """

    _context_binder: Any
    _context_fallback: dict

    def bind_to_framework(self, binder: Any) -> None:
        self._context_binder = binder

    @property
    def context(self) -> Any:
        binder = self._context_binder
        if binder is not None:
            return binder._get_context()
        return self._context_fallback

    @context.setter
    def context(self, value: Any) -> None:
        mapping = dict(value) if value is not None else {}
        binder = self._context_binder
        if binder is not None:
            binder._set_context(mapping)
            return
        self._context_fallback = mapping
