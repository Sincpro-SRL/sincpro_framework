"""The language of a call (#190, #270): an entrance reads what the caller prefers
(`Accept-Language`) and, when nothing set `lang`, the call is in the first of them — what a use
case reads in its context and what a refusal is told in. What a project says wins: the `lang`
the call carried, a provider that gives it.
"""

from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.common.failures import GUIDANCE_OF, TITLE_OF
from sincpro_framework.context import ContextKey, context_propagation, use_context
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.entrypoints.entrypoint.decorators import rest
from sincpro_framework.exceptions import FailureKind


class CommandGreet(DataTransferObject):
    name: str


class ResponseGreeting(DataTransferObject):
    lang: str | None
    preferred: list[str] | None


class CommandClose(DataTransferObject):
    month: str


def desk(lang_provider: bool = False) -> UseFramework:
    bus = UseFramework("desk", log_after_execution=False)

    if lang_provider:

        @bus.context_provider(needs=[], gives=[ContextKey.LANG])
        def saved_language(context: Any) -> dict[str, Any]:
            return {ContextKey.LANG: "fr"}  # the user's saved language

    @bus.feature(CommandGreet)
    @rest.post("/greetings")
    class Greet(Feature):
        def execute(self, dto: CommandGreet) -> ResponseGreeting:
            context = use_context()
            return ResponseGreeting(
                lang=context.get(ContextKey.LANG),
                preferred=context.get(ContextKey.PREFERRED_LANGS),
            )

    @bus.feature(CommandClose)
    @rest.post("/closings")
    class Close(Feature):
        def execute(self, dto: CommandClose) -> None:
            raise ddd_exceptions.DomainError(f"{dto.month} is already closed")

    return bus


def _client(bus: UseFramework) -> Any:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    return TestClient(FastApiGateway([bus]).app())


def test_accept_language_is_read_best_first():
    carried = context_propagation.extract(
        {"Accept-Language": "en;q=0.8, es-BO, *;q=0.1, de;q=0, es;q=0.9"}
    )

    assert carried[ContextKey.PREFERRED_LANGS] == ["es-BO", "es", "en"]


def test_a_use_case_reads_the_first_language_the_caller_prefers():
    answered = _client(desk()).post(
        "/desk/greetings",
        json={"name": "Ana"},
        headers={"Accept-Language": "es-BO, es;q=0.9"},
    )

    assert answered.json() == {"lang": "es-BO", "preferred": ["es-BO", "es"]}


def test_with_no_preference_the_call_has_no_language():
    answered = _client(desk()).post("/desk/greetings", json={"name": "Ana"})

    assert answered.json() == {"lang": None, "preferred": None}


def test_the_lang_the_call_carried_wins_over_what_the_caller_prefers():
    answered = _client(desk()).post(
        "/desk/greetings",
        json={"name": "Ana"},
        headers={"Accept-Language": "es", "Baggage": "lang=en"},
    )

    assert answered.json()["lang"] == "en"


def test_a_provider_that_gives_the_language_wins_over_what_the_caller_prefers():
    answered = _client(desk(lang_provider=True)).post(
        "/desk/greetings", json={"name": "Ana"}, headers={"Accept-Language": "es"}
    )

    assert answered.json() == {"lang": "fr", "preferred": ["es"]}


def test_a_refusal_is_told_in_the_language_the_caller_prefers():
    """#270: the title and the guidance of the kind, in Spanish; the error's own message as
    the use case wrote it."""
    answered = _client(desk()).post(
        "/desk/closings", json={"month": "2026-10"}, headers={"Accept-Language": "es"}
    )

    told = answered.json()
    assert answered.status_code == 422
    assert told["title"] == TITLE_OF[FailureKind.DOMAIN]["es"]
    assert told["guidance"] == GUIDANCE_OF[FailureKind.DOMAIN]["es"]
    assert told["detail"] == "2026-10 is already closed"


def test_a_refusal_with_no_preference_is_told_in_the_default_language():
    told = _client(desk()).post("/desk/closings", json={"month": "2026-10"}).json()

    assert told["guidance"] == GUIDANCE_OF[FailureKind.DOMAIN]["default"]
