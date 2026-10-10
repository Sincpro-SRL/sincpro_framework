"""Wherever a declaration takes several of something, one alone, a list or a tuple read the same.

A list is how a reader writes several things; one thing alone needs no wrapping; a tuple keeps
working for whoever wrote one.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.caching.domain.failure import FailSafe
from sincpro_framework.data_layer.caching.domain.policies import CachePolicy
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Derivations,
    Derive,
    Entity,
    Is,
    Presentation,
    When,
    presentation_of,
)
from sincpro_framework.ddd.criteria import Operator, Sort
from sincpro_framework.ddd.entity.entity_meta import derivations_of
from sincpro_framework.ddd.repositories import Hook, Hooks


@dataclass
class Line:
    qty: int = 1


@dataclass
class AsList(Entity):
    code: str = ""
    name: str = ""
    state: str = "draft"
    lines: list[Line] = field(default_factory=list)
    count: int = 0
    total: Decimal = Decimal("0")

    def count_of(self) -> int:
        return len(self.lines)

    def total_of(self) -> Decimal:
        return Decimal(self.count)

    presentation = Presentation["AsList"](
        readonly=lambda a: [a.code],
        required=lambda a: [a.name],
        readonly_when=lambda a: [When(Is(a.state, Operator.NE, "draft"), a.name)],
        required_when=lambda a: [When(Is(a.state, Operator.EQ, "draft"), a.code)],
        visible_when=lambda a: [When(Is(a.state, Operator.EQ, "draft"), a.total)],
    )
    derivations = Derivations["AsList"](
        lambda a: [
            Derive(a.count, depends=[a.lines], by=lambda r: len(r.lines)),
            Derive(a.total, depends=[a.count], by=lambda r: Decimal(r.count)),
        ]
    )


@dataclass
class AsTuple(Entity):
    code: str = ""
    name: str = ""
    state: str = "draft"
    lines: list[Line] = field(default_factory=list)
    count: int = 0
    total: Decimal = Decimal("0")

    presentation = Presentation["AsTuple"](
        readonly=lambda a: (a.code,),
        required=lambda a: (a.name,),
        readonly_when=lambda a: (When(Is(a.state, Operator.NE, "draft"), a.name),),
        required_when=lambda a: (When(Is(a.state, Operator.EQ, "draft"), a.code),),
        visible_when=lambda a: (When(Is(a.state, Operator.EQ, "draft"), a.total),),
    )
    derivations = Derivations["AsTuple"](
        lambda a: (
            Derive(a.count, depends=(a.lines,), by=lambda r: len(r.lines)),
            Derive(a.total, depends=(a.count,), by=lambda r: Decimal(r.count)),
        )
    )


@dataclass
class AsOne(Entity):
    code: str = ""
    name: str = ""
    state: str = "draft"
    lines: list[Line] = field(default_factory=list)
    count: int = 0
    total: Decimal = Decimal("0")

    presentation = Presentation["AsOne"](
        readonly=lambda a: a.code,
        required=lambda a: a.name,
        readonly_when=lambda a: When(Is(a.state, Operator.NE, "draft"), a.name),
        required_when=lambda a: When(Is(a.state, Operator.EQ, "draft"), a.code),
        visible_when=lambda a: When(Is(a.state, Operator.EQ, "draft"), a.total),
    )
    derivations = Derivations["AsOne"](
        lambda a: [
            Derive(a.count, depends=a.lines, by=lambda r: len(r.lines)),
            Derive(a.total, depends=a.count, by=lambda r: Decimal(r.count)),
        ]
    )


@dataclass
class OneDerive(Entity):
    lines: list[Line] = field(default_factory=list)
    count: int = 0

    derivations = Derivations["OneDerive"](
        lambda a: Derive(a.count, depends=a.lines, by=lambda r: len(r.lines))
    )


@dataclass
class OrderedAsOne(Entity):
    code: str = ""

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return Sort(field="code")  # type: ignore[return-value] — one alone, as a reader writes it


@dataclass
class OrderedAsList(Entity):
    code: str = ""

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return [Sort(field="code")]  # type: ignore[return-value]


@dataclass
class OrderedAsTuple(Entity):
    code: str = ""

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)


def test_a_default_order_reads_the_same_as_a_list_a_tuple_or_one_alone():
    orders = [
        presentation_of(one).order for one in (OrderedAsOne, OrderedAsList, OrderedAsTuple)
    ]

    assert orders == [(Sort(field="code"),)] * 3


def test_a_presentation_reads_the_same_as_a_list_a_tuple_or_one_alone():
    read = [presentation_of(one) for one in (AsList, AsTuple, AsOne)]

    first = read[0]
    for one in read[1:]:
        assert (one.readonly, one.required) == (first.readonly, first.required)
        for part in ("readonly_when", "required_when", "visible_when"):
            assert getattr(one, part) == getattr(first, part), part
    assert first.readonly >= {"code"} and first.required >= {"name"}
    assert set(first.visible_when) == {"total"}


def test_derivations_read_the_same_as_a_list_a_tuple_or_one_alone():
    read = [
        tuple((one.field, one.depends) for one in derivations_of(kind))
        for kind in (AsList, AsTuple, AsOne)
    ]

    assert read[0] == read[1] == read[2] == (("count", ("lines",)), ("total", ("count",)))
    assert tuple((one.field, one.depends) for one in derivations_of(OneDerive)) == (
        ("count", ("lines",)),
    )


@pytest.mark.parametrize("kind", [AsList, AsTuple, AsOne])
def test_a_save_computes_the_same_whichever_form_declared_it(kind):
    repository = MemoryRepository()
    record = kind(lines=[Line(), Line(), Line()])

    repository.save(record)

    assert (record.count, record.total) == (3, Decimal("3"))


@dataclass
class Box(Entity):
    label: str = ""


def placed(after: object) -> list[str]:
    hooks = Hooks(None).inject(UseFramework("one-or-several", log_after_execution=False))
    ran: list[str] = []

    @hooks.on(Box)
    class First(Hook):
        def before_save(self, box: Box) -> None:
            ran.append("first")

    @hooks.on(Box, after=after(First) if callable(after) else after)  # type: ignore[arg-type]
    class Second(Hook):
        def before_save(self, box: Box) -> None:
            ran.append("second")

    @hooks.on(Box, before=[Second], sequence=-5)
    class Zero(Hook):
        def before_save(self, box: Box) -> None:
            ran.append("zero")

    MemoryRepository(hooks=hooks).save(Box())
    return ran


@pytest.mark.parametrize(
    "written",
    [lambda first: first, lambda first: [first], lambda first: (first,)],
    ids=["one", "list", "tuple"],
)
def test_a_hook_is_placed_after_one_alone_a_list_or_a_tuple(written):
    assert placed(written) == ["zero", "first", "second"]


class Invoice:
    pass


class Customer:
    pass


def test_a_cache_policy_takes_one_key_alone_or_a_list_and_keeps_a_tuple():
    one = CachePolicy(ttl=timedelta(minutes=1), depends_on=[Invoice], vary_by="tenant")
    several = CachePolicy(
        ttl=timedelta(minutes=1), depends_on=[Invoice, Customer], vary_by=["tenant", "lang"]
    )
    kept = CachePolicy(ttl=timedelta(minutes=1), depends_on=(Invoice,), vary_by=("tenant",))

    assert (one.depends_on, one.vary_by) == ((Invoice,), ("tenant",))
    assert hash(one) == hash(kept)
    assert (several.depends_on, several.vary_by) == ((Invoice, Customer), ("tenant", "lang"))
    assert (kept.depends_on, kept.vary_by) == (one.depends_on, one.vary_by)


@pytest.mark.parametrize(
    "errors",
    [TimeoutError, [TimeoutError, ConnectionError], (TimeoutError, ConnectionError)],
    ids=["one", "list", "tuple"],
)
def test_fail_safe_takes_one_error_alone_or_a_list(errors):
    policy = FailSafe(serve_for=timedelta(minutes=5), errors=errors)

    assert policy.handles(TimeoutError()) is True
    assert policy.handles(KeyError()) is False
    assert isinstance(policy.errors, tuple)
