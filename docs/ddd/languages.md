# Languages: a text in several, the language of a call, and messages (design, #190, #187)

> Status: **piece 1 built** — `MultiLanguage`, `ContextKey.LANG`, the labels and `TranslatedText` read as
> one (`tests/ddd/test_multi_language.py`). Pieces 2 and 3 are the design still to build; this page
> becomes the component's when they are.

Three pieces, built in this order, each on the one before:

1. **`MultiLanguage`** — a text in several languages, one value, as `MultiCurrency` is an amount in
   several currencies: read by its language, stored as one JSON document.
2. **The language of a call** (#190) — negotiated at the edge, carried in the context, answered back.
3. **Messages** (#187) — a key, its parameters and a default text, rendered in the call's language:
   what an error, a notification or a label says.

## What exists today

| Piece | What it is | The gap |
|---|---|---|
| `TranslatedText` | a column type: `{"default": "Hola", "en": "Hello"}` in a text column, searched with `like` | a `dict[str, str]` in Python — no value, no fallback, no language of the call |
| `Translated = dict[str, str]` | what an Entity's and an enum's labels are (`presentation`, `translations()`) | the same dict, read by hand |
| `"lang"` in the context | an example key in the docs | not a key of the framework: nobody negotiates it, nobody answers it |
| `Problem.detail` | English text | no key a client translates by, no language |

## What mature frameworks do

**Storage: the same four ways money had.**

| Way | Who | Kept | For |
|---|---|---|---|
| **One JSON column** | Odoo 16+ (`{"en_US": …, "es_BO": …}`, `en_US` the fallback, replacing `ir_translation`); Rails Mobility's *container* backend | one `jsonb` per field | any number of languages, nothing to migrate when one is added |
| **A column per language** | django-modeltranslation (`title_en`, `title_es`) ; Mobility *column* | `title_en`, `title_es` | few, fixed languages; plain indexes |
| **A translations table** | django-parler, Rails Globalize / Mobility *table* | `post_translations(post_id, locale, title)` | many languages added at run time; one more query or join |
| **Key–value** | Mobility's default *key_value* | one shared table of (model, id, attribute, locale, value) | no schema change per attribute; a join per read |

**The language of a request:**

- Language tags are **BCP 47** (`es-BO`); a missing one falls back by **RFC 4647 lookup** —
  `es-BO` → `es` → the default — so a service offering only `es` answers an `es-BO` reader.
- The request says it in `Accept-Language` (with q-values), a `?lang=` or a cookie, in an order the
  service chooses (ASP.NET's request localization providers, Django's `LocaleMiddleware`); the
  answer says `Content-Language`, and a cache varies by it.
- The language is the request's, not the process's: Django and Rails keep it per request, Odoo in
  `env.context["lang"]`.

**Messages:** a key, named parameters and the text per language, plurals and selections by the
language's rules (ICU MessageFormat, CLDR plural categories); in Python, `gettext` in the standard
library and Babel / PyICU for the rest.

Sources: [Odoo 16 ORM changelog](https://www.odoo.com/documentation/saas-16.1/developer/reference/backend/orm/changelog.html),
[Odoo PR 97692](https://mergebot.odoo.com/odoo/odoo/pull/97692),
[manaTec — translations from Odoo 16](https://www.manatec.de/en_US/blog/manablog-1/ubersetzungswerte-ab-odoo-16-150),
[Mobility](https://www.rubydoc.info/gems/mobility/Mobility), [JSONify your Ruby translations](https://dejimata.com/2018/3/20/jsonify-your-ruby-translations),
[django-parler — background](https://django-parler.readthedocs.io/en/stable/background.html),
[RFC 4647](https://www.rfc-editor.org/rfc/rfc4647), [Microsoft — locale fallback](https://learn.microsoft.com/sv-se/globalization/locale/fallback),
[ICU MessageFormat guide](https://simplelocalize.io/blog/posts/what-is-icu/).

## 1. `MultiLanguage`

```
title = MultiLanguage("Factura", en="Invoice", es_BO="Factura (Bolivia)")   the first text is the default
                         a region by keyword with `_` (es_BO, pt_BR, zh_Hant_TW), kept as BCP 47's `es-BO`
title["en"]               "Invoice"
title.in_("es-BO")        "Factura"     RFC 4647: es-BO → es → the default
str(title)                the call's language (from the context), else the default
title.codes               ("default", "en", "pt")
title.with_(fr="Facture") a new value; the text it was is untouched
"en" in title, len(title), iteration over (language, text)
```

- One JSON document, the shape `TranslatedText` and the labels already have:
  `{"default": "Factura", "en": "Invoice"}` — a `default` always, the text every reader falls back to.
- Equal and hashable by value; a frozen DTO, so it travels in a Command, a Response, an MCP tool.
- Kept as `TypedJson(MultiLanguage)` (`jsonb`, filtered and indexed by expression as money is) or
  `TranslatedText` (a text column searched with `like` in every language at once).
- The labels an Entity, an enum and `presentation` answer become `MultiLanguage` — read the same,
  no dict by hand.

## 2. The language of a call (#190)

```
ContextKey.LANG = "lang"                      a BCP 47 tag, in the context like the tenant

REST / FastAPI / JSON-RPC:  ?lang=  →  Accept-Language (q-values)  →  the service's default
                            each matched against the languages the service offers (RFC 4647)
                            answered with Content-Language, and Vary: Accept-Language
MCP, gRPC, a queue, the remote bus: the context as it travels — the caller's language goes along
a use case:   use_context()[ContextKey.LANG]   or   str(title)   which reads it
```

- What a service offers and its default: `UseFramework(..., languages=("es", "en"))`, else
  everything asked is taken as asked; the default `"default"` — the text every value has.
- The order of the sources is the gateway's to change (`language_from=(QUERY, HEADER)`).
- Nothing else reads it implicitly: a use case that answers in a language reads it, as it reads
  the tenant (`CMD → Response`: the language never changes what the use case does, only which text
  it shows).

## 3. Messages (#187)

```
Message("invoice.not_found", "Invoice {number} does not exist", number="F-9")
    key, default text, parameters

messages = InMemoryMessages({"es": {"invoice.not_found": "La factura {number} no existe"}})
messages.render(message, "es-BO")     "La factura F-9 no existe"      RFC 4647, else the default text
```

- The text is a template of the standard library (`{name}`), so nothing is needed to start; a port,
  `Messages`, lets a project keep its catalog where it wants (a table, files, a service) and render
  plurals and selections with ICU or Babel behind an optional extra.
- **An error says a message**: `class InvoiceNotFound(ClientError): message = Message(...)`; its
  `Problem` carries `message_key` and `message_params` — a client that translates itself does — and
  `detail` rendered in the call's language at the edge, by the gateway's `Messages`.
- A notification, an email, a label: `messages.render(message, use_context()[ContextKey.LANG])`.

## Open questions

1. **The name**: `MultiLanguage`, the pair of `MultiCurrency` — or `Translated` (taking the name the
   labels' dict has today)?
2. **`str(title)` reading the call's language** from the context — or only explicit `title.in_(lang)`?
   Proposed: both; `str` is what a template, a log and an f-string read.
3. **The labels** (`presentation`, enums) moving from `dict[str, str]` to `MultiLanguage` — a break,
   the JSON they answer unchanged.
4. **The order**: 1 → 2 → 3, each built and released alone?
