"""The remote bus contract (PRD_23): one set of cases, run on a context executed here, over gRPC
and over HTTP — what passes on one passes on the three.

Service B hosts `shop`; service A is a `shop` bus built from the same code and pointed at B by
configuration only. A's code — `shop(command, Response)`, an ApplicationService injected with it,
an async caller, a subscriber — is the code it would be if `shop` ran here.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest
from dependency_injector import providers
from pydantic import Secret, SecretStr
from structlog.testing import capture_logs

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)
from sincpro_framework.context import requires_context
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.entrypoints.catalog import Catalog
from sincpro_framework.event_driven.services.subscriber import Subscriber
from sincpro_framework.exceptions import ContextRequired, UnknownDTOToExecute
from sincpro_framework.introspection.operations import operations_of
from sincpro_framework.remote_execution import (
    ContextFailed,
    ContextTimeout,
    ContextUnavailable,
    DTODoesNotFit,
)
from sincpro_framework.sincpro_conf import settings
from sincpro_framework.transport.failures import FailureKind, refined_failure_kind
from tests.remote_execution.hosting import TRANSPORTS, host_over

WHERE = ("local", *TRANSPORTS)


class Currency(StrEnum):
    BOB = "BOB"
    USD = "USD"


class Line(DataTransferObject):
    sku: str
    quantity: int


class CommandQuote(DataTransferObject):
    sku: str
    quantity: int = 1


class ResponseQuote(DataTransferObject):
    total: Decimal
    lines: list[Line]
    currency: Currency


class CommandLines(DataTransferObject):
    count: int


class CommandAnything(DataTransferObject):
    shape: str


class CommandRead(DataTransferObject):
    pass


class ResponseRead(DataTransferObject):
    types: dict[str, str]
    secret: str


class CommandFail(DataTransferObject):
    how: str


class CommandSlow(DataTransferObject):
    seconds: float


class CommandGated(DataTransferObject):
    pass


@dataclass(kw_only=True)
class StockPicked(DomainEvent):
    name = "contract.v1.stock_picked"
    sku: str = ""


class OverCredit(DomainError):
    """A refusal with fields of its own, and a constructor that takes them."""

    def __init__(self, customer: str, limit: Decimal) -> None:
        super().__init__(f"{customer} is over its {limit} credit limit")
        self.customer = customer
        self.limit = limit


class Heard:
    """Where each execution ran, and the events B heard."""

    def __init__(self) -> None:
        self.places: list[str] = []
        self.events: list[str] = []


def _shop(place: str, heard: Heard, engine: Any = "an engine") -> UseFramework:
    """The `shop` context, as every service builds it from the same code."""
    shop = UseFramework("contract-shop", log_after_execution=False)
    shop.add_dependency("heard", heard)
    shop.add_dependency("place", place)
    shop.add_dependency("engine", engine)

    @shop.feature(CommandQuote)
    class Quote(Feature):
        heard: Heard
        place: str
        engine: Any

        def execute(self, dto: CommandQuote) -> ResponseQuote:
            self.heard.places.append(self.place)
            return ResponseQuote(
                total=Decimal("2.50") * dto.quantity,
                lines=[Line(sku=dto.sku, quantity=dto.quantity)],
                currency=Currency.BOB,
            )

    @shop.feature(CommandLines)
    class Lines(Feature):
        def execute(self, dto: CommandLines) -> list[Line]:
            return [Line(sku=f"S-{one}", quantity=one) for one in range(dto.count)]

    @shop.feature(CommandAnything)
    class Anything(Feature):
        def execute(self, dto: CommandAnything) -> Any:
            if dto.shape == "dto":
                return Line(sku="A", quantity=1)
            if dto.shape == "list":
                return [Line(sku="A", quantity=1), Line(sku="B", quantity=2)]
            if dto.shape == "none":
                return None
            return {"sku": "A", "lines": [Line(sku="A", quantity=1)]}

    @shop.feature(CommandRead)
    class Read(Feature):
        def execute(self, dto: CommandRead) -> ResponseRead:
            context = dict(self.context)
            secret = context.get("TOKEN")
            return ResponseRead(
                types={key: type(value).__name__ for key, value in context.items()},
                secret=secret.get_secret_value() if secret is not None else "",
            )

    @shop.feature(CommandFail)
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            if dto.how == "domain":
                raise OverCredit("acme", Decimal("100"))
            if dto.how == "builtin":
                raise ValueError("the quantity is negative")

            class OnlyHere(Exception):
                """A class only the host knows."""

            raise OnlyHere("raised where it runs")

    @shop.feature(CommandSlow)
    class Slow(Feature):
        def execute(self, dto: CommandSlow) -> None:
            import time

            time.sleep(dto.seconds)

    @shop.feature(CommandGated)
    @requires_context("TENANT")
    class Gated(Feature):
        def execute(self, dto: CommandGated) -> None: ...

    @shop.feature(StockPicked)
    class Picked(Feature):
        heard: Heard

        def execute(self, dto: StockPicked) -> None:
            self.heard.events.append(dto.sku)

    return shop


class Exploding:
    """A dependency that cannot be built where `shop` does not run."""

    def __init__(self) -> None:
        raise RuntimeError("DB_URL is not configured here")


class Service:
    """Service A's `shop` — a reference to B's, or the context itself for `local` — and B's
    record of what ran there."""

    def __init__(self, shop: UseFramework, on_b: Heard) -> None:
        self.shop = shop
        self.on_b = on_b


def _served(where: str) -> Iterator[Service]:
    on_b = Heard()
    if where == "local":
        yield Service(_shop("B", on_b), on_b)
        return
    for address in host_over(where, [_shop("B", on_b)]):
        caller = _shop("A", Heard(), engine=providers.Singleton(Exploding))
        caller.hosted_by(f"{address}?timeout=5")
        yield Service(caller, on_b)


@pytest.fixture(params=WHERE)
def service(request: pytest.FixtureRequest) -> Iterator[Service]:
    yield from _served(request.param)


@pytest.fixture(params=TRANSPORTS)
def remote(request: pytest.FixtureRequest) -> Iterator[Service]:
    yield from _served(request.param)


# ---------------------------------------------------------------------------------------------
# Rules 1 and 2 — the answer is the DTO, never a dict
# ---------------------------------------------------------------------------------------------


def test_the_answer_is_an_instance_of_the_response_given(service):
    answer = service.shop(CommandQuote(sku="P-1", quantity=2), ResponseQuote)

    assert isinstance(answer, ResponseQuote)
    assert answer.total == Decimal("5.00") and answer.currency is Currency.BOB
    assert answer.lines == [Line(sku="P-1", quantity=2)]
    assert service.on_b.places == ["B"]


def test_without_a_response_the_answer_is_the_type_the_handler_declares(service):
    assert isinstance(service.shop(CommandQuote(sku="P-1")), ResponseQuote)
    assert service.shop(CommandLines(count=2)) == [
        Line(sku="S-0", quantity=0),
        Line(sku="S-1", quantity=1),
    ]


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("dto", Line(sku="A", quantity=1)),
        ("list", [Line(sku="A", quantity=1), Line(sku="B", quantity=2)]),
        ("none", None),
        ("values", {"sku": "A", "lines": [Line(sku="A", quantity=1)]}),
    ],
)
def test_a_handler_declaring_any_answers_dtos_as_dtos(service, shape, expected):
    answer = service.shop(CommandAnything(shape=shape))

    assert answer == expected
    if isinstance(expected, Line):
        assert type(answer) is Line


# ---------------------------------------------------------------------------------------------
# Rule 3 — versions are not compared, DTOs are fitted
# ---------------------------------------------------------------------------------------------


def _same_name_as(original: type, newer: type) -> type:
    """`newer` registered under `original`'s name — another deployment's version of it."""
    newer.__module__, newer.__qualname__ = original.__module__, original.__qualname__
    return newer


def test_a_newer_caller_with_a_field_the_host_does_not_know_is_answered(remote):
    class Newer(DataTransferObject):
        sku: str
        quantity: int = 1
        gift_wrap: bool = True

    answer = remote.shop(_same_name_as(CommandQuote, Newer)(sku="P-1"), ResponseQuote)

    assert answer.lines == [Line(sku="P-1", quantity=1)]


def test_an_older_caller_without_a_field_that_has_a_default_is_answered(remote):
    class Older(DataTransferObject):
        sku: str

    answer = remote.shop(_same_name_as(CommandQuote, Older)(sku="P-1"), ResponseQuote)

    assert answer.lines == [Line(sku="P-1", quantity=1)]


def test_a_dto_missing_a_required_field_does_not_fit_and_says_which(remote):
    class Broken(DataTransferObject):
        quantity: int

    with pytest.raises(DTODoesNotFit, match="sku: Field required") as refused:
        remote.shop(_same_name_as(CommandQuote, Broken)(quantity=1))

    assert refused.value.fields == ["sku: Field required"]
    assert refined_failure_kind(refused.value) == FailureKind.INVALID


def test_an_answer_missing_a_field_the_caller_needs_does_not_fit_and_says_which(remote):
    class ResponseQuoteNewer(DataTransferObject):
        total: Decimal
        discount: Decimal

    with pytest.raises(DTODoesNotFit, match="the answer of contract-shop .* discount"):
        remote.shop(CommandQuote(sku="P-1"), ResponseQuoteNewer)


def test_a_dto_the_host_does_not_answer_is_unknown_there(remote):
    """Renaming or moving a DTO's class is the one change that breaks a caller — and it says so."""

    class CommandRenamed(DataTransferObject):
        sku: str

    with pytest.raises(UnknownDTOToExecute, match="does not answer .*CommandRenamed"):
        remote.shop(CommandRenamed(sku="P-1"))


# ---------------------------------------------------------------------------------------------
# Rule 4 — a reference is never built, by any path
# ---------------------------------------------------------------------------------------------


def test_a_reference_is_never_built_by_any_path(remote):
    shop = remote.shop

    shop(CommandQuote(sku="sync"), ResponseQuote)
    asyncio.run(shop.get_async_bus().execute(CommandQuote(sku="async"), ResponseQuote))
    Subscriber(shop).handle(StockPicked(sku="event"))
    with shop.with_trace() as traced:
        traced(CommandQuote(sku="traced"), ResponseQuote)
    with shop.with_parent_trace() as adopted:
        adopted(CommandQuote(sku="adopted"), ResponseQuote)
    assert "CommandQuote" in operations_of(shop)
    assert {one.name for one in Catalog(shop).get_scalar_use_cases()} >= {"CommandQuote"}
    shop.build_root_bus()

    assert shop.is_reference and shop.is_ready
    assert not shop.was_initialized and shop.bus is None
    assert remote.on_b.places == ["B"] * 4 and remote.on_b.events == ["event"]


def test_a_reference_answers_its_registrations_without_building(remote):
    names = set(remote.shop.dto_registry)

    assert {f"{__name__}.CommandQuote", StockPicked.name} <= names
    assert not remote.shop.was_initialized


# ---------------------------------------------------------------------------------------------
# Rule 5 — injected as a bus
# ---------------------------------------------------------------------------------------------


class CommandSell(DataTransferObject):
    sku: str


def _sales(shop: UseFramework) -> UseFramework:
    sales = UseFramework("contract-sales", log_after_execution=False)
    sales.add_dependency("shop", shop)

    @sales.app_service(CommandSell)
    class Sell(ApplicationService):
        shop: UseFramework

        def execute(self, dto: CommandSell) -> ResponseQuote:
            quoted = self.shop(CommandQuote(sku=dto.sku, quantity=3), ResponseQuote)
            assert quoted is not None
            return quoted

    return sales


def test_another_context_injected_with_it_calls_it_as_a_bus(service):
    sales = _sales(service.shop)

    answer = sales(CommandSell(sku="P-9"), ResponseQuote)
    later = asyncio.run(sales.get_async_bus().execute(CommandSell(sku="P-8"), ResponseQuote))

    assert answer is not None and answer.lines == [Line(sku="P-9", quantity=3)]
    assert later is not None and later.lines == [Line(sku="P-8", quantity=3)]
    assert service.on_b.places == ["B", "B"]


# ---------------------------------------------------------------------------------------------
# Rule 6 — the context travels as it is
# ---------------------------------------------------------------------------------------------


def test_the_context_travels_with_its_types_secrets_included(service):
    context = {
        "TOKEN": SecretStr("s3cr3t"),
        "PIN": Secret(1234),
        "currency": Currency.USD,
        "limit": Decimal("10.5"),
        "line": Line(sku="ctx", quantity=1),
    }

    with service.shop.context(context):
        answer = service.shop(CommandRead(), ResponseRead)

    assert answer is not None and answer.secret == "s3cr3t"
    assert {key: answer.types[key] for key in context} == {
        "TOKEN": "SecretStr",
        "PIN": "Secret",
        "currency": "Currency",
        "limit": "Decimal",
        "line": "Line",
    }


# ---------------------------------------------------------------------------------------------
# Rule 7 — an error arrives as itself
# ---------------------------------------------------------------------------------------------


def test_an_error_arrives_as_itself_with_its_attributes(service):
    with pytest.raises(OverCredit, match="acme is over its 100") as refused:
        service.shop(CommandFail(how="domain"))
    with pytest.raises(ValueError, match="quantity is negative"):
        service.shop(CommandFail(how="builtin"))
    with pytest.raises(ContextRequired) as required:
        service.shop(CommandGated())

    assert refused.value.customer == "acme" and refused.value.limit == Decimal("100")
    assert required.value.missing == ["TENANT"]


def test_an_error_whose_class_only_the_host_knows_is_context_failed(remote):
    with pytest.raises(ContextFailed, match="raised where it runs") as failed:
        remote.shop(CommandFail(how="only-here"))

    assert failed.value.kind.endswith("OnlyHere")


# ---------------------------------------------------------------------------------------------
# Rule 8 — what only a network can do is named
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", TRANSPORTS)
def test_a_host_that_is_down_did_not_run_it(scheme):
    shop = _shop("A", Heard(), engine=providers.Singleton(Exploding))
    shop.hosted_by(f"{scheme}://127.0.0.1:9?timeout=1")

    with pytest.raises(ContextUnavailable) as unavailable:
        shop(CommandQuote(sku="P-1"))

    assert refined_failure_kind(unavailable.value) == FailureKind.UNAVAILABLE


def test_a_deadline_passed_after_the_call_was_sent_may_have_run(remote):
    remote.shop.hosted_by(f"{remote.shop.hosted_at}".replace("timeout=5", "timeout=0.2"))

    with pytest.raises(ContextTimeout, match="within 0.2s") as late:
        remote.shop(CommandSlow(seconds=1))

    assert refined_failure_kind(late.value) == FailureKind.UNKNOWN_OUTCOME


# ---------------------------------------------------------------------------------------------
# Where a context runs — here, unless this process's map names it elsewhere
# ---------------------------------------------------------------------------------------------


def test_a_context_served_here_runs_here_whatever_the_map_says(remote):
    """B's map may name B itself: what it serves it runs, rather than forwarding to itself."""
    served = _shop("B", remote.on_b)
    served.hosted_by(f"{remote.shop.hosted_at}")
    served.run_here()

    answer = served(CommandQuote(sku="P-1"), ResponseQuote)

    assert answer is not None and not served.is_reference and served.was_initialized
    assert remote.on_b.places == ["B"]


def test_nothing_configured_runs_here_as_it_always_did(monkeypatch):
    monkeypatch.delenv("SINCPRO_CONTEXT_MAP", raising=False)
    monkeypatch.setattr(settings, "context_map_override", None)
    on_here = Heard()

    shop = _shop("here", on_here)
    answer = shop(CommandQuote(sku="P-1"), ResponseQuote)

    assert shop.hosted_at is None and not shop.is_reference and shop.was_initialized
    assert answer is not None and on_here.places == ["here"]


def test_the_environment_is_read_even_when_the_projects_conf_file_does_not_declare_it(
    monkeypatch,
):
    """A project's own conf file replaces the framework's, `$ENV:` sentinels and all — the map a
    deployment sets in its environment still reaches every bus."""
    monkeypatch.setattr(settings, "context_map_override", None)
    monkeypatch.setenv("SINCPRO_CONTEXT_MAP", "contract-shop=grpc://elsewhere:1?timeout=2")

    shop = _shop("A", Heard(), engine=providers.Singleton(Exploding))

    assert shop.is_reference and str(shop.hosted_at) == "grpc://elsewhere:1?timeout=2"


def test_a_reference_says_where_it_forwards_when_it_is_created(monkeypatch):
    monkeypatch.setenv("SINCPRO_CONTEXT_MAP", "contract-shop=http://billing-svc:8000")

    with capture_logs() as logs:
        _shop("A", Heard())

    assert any(
        "contract-shop is hosted at http://billing-svc:8000" in one["event"] for one in logs
    )
