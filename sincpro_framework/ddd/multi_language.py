"""`MultiLanguage`: a text in several languages, one value — read by its language as `MultiCurrency`
is read by its currency, and stored as one JSON document, the shape Odoo and every label here have.

    title = MultiLanguage("Factura", en="Invoice", es_BO="Factura (Bolivia)")   the first text is the default
                                       a region as a keyword: `es_BO`, `pt_BR`, `zh_Hant_TW` — kept as `es-BO`
    title["en"]                ->  "Invoice"
    title.in_("es-BO")         ->  "Factura"     RFC 4647: es-BO → es → the default
    str(title)                 ->  the text in the call's language (`ContextKey.LANG`), else the default
    title.with_(fr="Facture")  ->  a new value; the text it was is untouched
    MultiLanguage({"default": "Factura", "es-BO": "Factura (Bolivia)"})     or all of them as a mapping

Context: a language is a BCP 47 tag (`es-BO`), written as the standard writes it whatever it was
given as (`es_bo` → `es-BO`); one not held falls back by RFC 4647 lookup — the tag cut a subtag at a
time, then `default`. It is a `Mapping[str, str]` too, so whatever read the translations as a dict
reads them still. JSON is the dict itself: `{"default": "Factura", "en": "Invoice"}`.
"""

from collections.abc import Iterator, Mapping
from typing import Any, Self

from pydantic import ConfigDict, RootModel, model_validator

from sincpro_framework.context import ContextKey, use_context

DEFAULT = "default"
"""The text every reader falls back to."""


def language_tag(tag: str) -> str:
    """`tag` as BCP 47 writes it: the language in lower case, a region in upper case, a script
    in title case — `es_bo` and `ES-bo` are `es-BO`, `zh-hant-tw` is `zh-Hant-TW`; `default` stays.
    """
    if tag.strip().lower() == DEFAULT:
        return DEFAULT
    parts = tag.strip().replace("_", "-").split("-")
    written = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 or (len(part) == 3 and part.isdigit()):
            written.append(part.upper())
        elif len(part) == 4:
            written.append(part.title())
        else:
            written.append(part.lower())
    return "-".join(written)


def fallbacks(tag: str) -> list[str]:
    """The tags RFC 4647's lookup tries for `tag`, most specific first — a subtag cut at a time,
    a single-letter one cut with the one after it — and `default` last.

        fallbacks("es-BO")         ->  ["es-BO", "es", "default"]
        fallbacks("zh-Hant-TW")    ->  ["zh-Hant-TW", "zh-Hant", "zh", "default"]
    """
    parts = language_tag(tag).split("-")
    tried = []
    while parts:
        tried.append("-".join(parts))
        parts = parts[:-1]
        if parts and len(parts[-1]) == 1:
            parts = parts[:-1]
    return [one for one in tried if one != DEFAULT] + [DEFAULT]


class MultiLanguage(RootModel[dict[str, str]], Mapping[str, str]):
    """A text in several languages. See the module."""

    model_config = ConfigDict(frozen=True)

    def __init__(self, default: str | Mapping[str, str] = "", /, **texts: str) -> None:
        """`default` the text every reader falls back to — or all of them at once, a mapping by
        language — and each other language's by its tag."""
        held = (
            dict(default)
            if isinstance(default, Mapping)
            else {DEFAULT: default} if default else {}
        )
        super().__init__({**held, **texts})

    @model_validator(mode="before")
    @classmethod
    def _tags(cls, value: Any) -> Any:
        """Each language written as BCP 47 writes it; a text not a `str` refused."""
        if not isinstance(value, Mapping):
            return value
        return {language_tag(str(tag)): text for tag, text in value.items()}

    # a mapping by language

    def __getitem__(self, tag: str) -> str:
        """The text in exactly `tag` — `KeyError` when it holds none; `in_` falls back."""
        return self.root[language_tag(tag)]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __contains__(self, tag: object) -> bool:
        return isinstance(tag, str) and language_tag(tag) in self.root

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self.root == {language_tag(str(tag)): text for tag, text in other.items()}
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.root.items())))

    @property
    def codes(self) -> tuple[str, ...]:
        """The languages it holds."""
        return tuple(self.root)

    # reading it in a language

    def in_(self, tag: str | None = None) -> str:
        """The text for `tag` — itself, else the tag cut a subtag at a time (RFC 4647), else the
        default, else `""` — or, with no tag, the default."""
        for tried in fallbacks(tag) if tag else [DEFAULT]:
            if tried in self.root:
                return self.root[tried]
        return ""

    def __str__(self) -> str:
        """The text in the language of the call — `ContextKey.LANG` in the context — else the
        default: what a template, a log line and an f-string read."""
        return self.in_(use_context().get(ContextKey.LANG))

    def __repr__(self) -> str:
        return f"MultiLanguage({self.root!r})"

    # changing it

    def with_(self, texts: Mapping[str, str] | None = None, /, **more: str) -> Self:
        """A new value with these texts — each one held replaced, the others kept."""
        return type(self)({**self.root, **dict(texts or {}), **more})
