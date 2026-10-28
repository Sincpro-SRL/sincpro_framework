"""`MultiLanguage`: a text in several languages, read by a BCP 47 tag with RFC 4647's fallback, in the
language of the call when nothing is said — a mapping by language still, one JSON document stored.
"""

from collections.abc import Mapping

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.context import ContextKey
from sincpro_framework.ddd import MultiLanguage
from sincpro_framework.ddd.multi_language import fallbacks, language_tag

INVOICE = MultiLanguage({"default": "Factura", "es-BO": "Factura (Bolivia)", "en": "Invoice"})


@pytest.mark.parametrize(
    ("given", "written"),
    [("es-BO", "es-BO"), ("es_bo", "es-BO"), ("ES-bo", "es-BO"), ("zh-hant-tw", "zh-Hant-TW"),
     ("es-419", "es-419"), ("DEFAULT", "default")],
)  # fmt: skip
def test_a_tag_is_written_as_bcp_47_writes_it_whatever_it_came_as(given, written):
    assert language_tag(given) == written


def test_a_region_falls_back_to_its_language_then_to_the_default():
    assert fallbacks("es-BO") == ["es-BO", "es", "default"]
    assert fallbacks("zh-Hant-TW") == ["zh-Hant-TW", "zh-Hant", "zh", "default"]
    assert INVOICE.in_("es-BO") == "Factura (Bolivia)"  # its own text
    assert INVOICE.in_("es_bo") == "Factura (Bolivia)"  # Odoo's way of writing it
    assert INVOICE.in_("es-AR") == "Factura"  # no es-AR, no es: the default
    assert INVOICE.in_("en-GB") == "Invoice"  # en-GB → en
    assert INVOICE.in_() == "Factura"


def test_it_is_read_by_its_language_as_a_mapping_is():
    assert INVOICE["en"] == "Invoice" and INVOICE["es_BO"] == "Factura (Bolivia)"
    assert "EN" in INVOICE and "fr" not in INVOICE and len(INVOICE) == 3
    assert INVOICE.codes == ("default", "es-BO", "en")
    assert isinstance(INVOICE, Mapping) and dict(INVOICE)["en"] == "Invoice"
    assert INVOICE.get("fr") is None
    with pytest.raises(KeyError):
        INVOICE["fr"]  # exact; `in_` is the one that falls back


def test_a_regional_tag_is_a_keyword_with_an_underscore():
    title = MultiLanguage(
        "Factura", es_BO="Factura (Bolivia)", pt_BR="Fatura", zh_Hant_TW="發票"
    )

    assert title.codes == ("default", "es-BO", "pt-BR", "zh-Hant-TW")  # written as BCP 47
    assert title.in_("es-BO") == "Factura (Bolivia)" and title["pt-BR"] == "Fatura"
    assert title.with_(es_AR="Factura (Argentina)")["es-AR"] == "Factura (Argentina)"


def test_it_is_written_by_keyword_or_by_mapping_and_equal_by_value():
    by_keyword = MultiLanguage("Factura", en="Invoice")

    assert by_keyword == {"default": "Factura", "en": "Invoice"}
    assert by_keyword == MultiLanguage({"DEFAULT": "Factura", "EN": "Invoice"})
    assert {by_keyword, MultiLanguage({"default": "Factura", "en": "Invoice"})} == {
        by_keyword
    }
    changed = by_keyword.with_({"es-BO": "Factura (Bolivia)"}, fr="Facture")
    assert changed.codes == ("default", "en", "es-BO", "fr") and len(by_keyword) == 2


def test_str_reads_the_language_of_the_call():
    billing = UseFramework("languages", log_after_execution=False)

    assert str(INVOICE) == "Factura"
    with billing.context({ContextKey.LANG: "es-BO"}):
        assert f"{INVOICE}" == "Factura (Bolivia)"
    with billing.context({ContextKey.LANG: "en-US"}):
        assert str(INVOICE) == "Invoice"


def test_it_travels_as_the_json_object_odoo_and_every_label_here_have():
    assert INVOICE.model_dump(mode="json") == {
        "default": "Factura",
        "es-BO": "Factura (Bolivia)",
        "en": "Invoice",
    }
    assert MultiLanguage.model_validate_json('{"default": "a", "es_BO": "b"}')["es-BO"] == "b"
