"""`BusRegistry`: use cases stored as source, loaded onto a new generation of the bus — built,
checked, and swapped in whole, or refused with the bus left as it was."""

import sys
import threading
import traceback
from decimal import Decimal
from typing import Any

import pytest

from sincpro_framework.introspection import features
from sincpro_framework.runtime_use_cases import (
    BusRegistry,
    InMemoryUseCases,
    RuntimeUseCase,
    UseCaseRefused,
)
from tests.runtime_use_cases.billing import (
    CHECKOUT,
    DOUBLE_QUOTE,
    FEATURE_FOR_CHECKOUT,
    QUOTE,
    TAX_WITH_EXEMPTION,
    CommandComputeTax,
    ComputeTax,
    ResponseComputeTax,
    billing,
)

QUOTE_COMMAND = "sincpro_runtime.billing.quote.CommandQuote"


def _registry(*use_cases: RuntimeUseCase) -> BusRegistry:
    store = InMemoryUseCases()
    for use_case in use_cases:
        store.save(use_case)
    return BusRegistry(billing, store)


def test_a_stored_feature_answers_beside_the_ones_in_code():
    registry = _registry(RuntimeUseCase("quote", QUOTE))

    quoted = registry.execute(QUOTE_COMMAND, {"amount": "100"})
    taxed = registry.current(CommandComputeTax(amount=Decimal("100")), ResponseComputeTax)

    assert quoted.total == Decimal("113.00")
    assert taxed.tax == Decimal("13.00")


def test_a_stored_application_service_calls_the_features_in_code():
    registry = _registry(RuntimeUseCase("checkout", CHECKOUT))

    answer = registry.execute(
        "sincpro_runtime.billing.checkout.CommandCheckout", {"amount": 100}
    )

    assert answer.total == Decimal("113.00")


def test_a_new_version_answers_after_reload_and_the_old_bus_keeps_answering():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    before = registry.current
    old_command = before.dto_registry[QUOTE_COMMAND]

    registry.store.save(RuntimeUseCase("quote", QUOTE.replace("1.13", "1.16"), version=2))
    swapped = registry.reload()

    assert swapped
    assert registry.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("116.00")
    answered_before: Any = before(old_command(amount=Decimal("100")))
    assert answered_before.total == Decimal("113.00")


def test_nothing_changed_is_no_new_generation():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    bus = registry.current

    assert not registry.reload()
    assert registry.current is bus


def test_an_inactive_use_case_is_not_loaded():
    registry = _registry(RuntimeUseCase("quote", QUOTE, active=False))

    assert QUOTE_COMMAND not in registry.current.dto_registry


def test_a_retired_use_case_leaves_the_bus_on_reload():
    registry = _registry(RuntimeUseCase("quote", QUOTE))

    registry.store.save(RuntimeUseCase("quote", QUOTE, version=2, active=False))
    registry.reload()

    assert QUOTE_COMMAND not in registry.current.dto_registry


@pytest.mark.parametrize(
    "source, reason",
    [
        ("class Quote(Feature)\n", r"<runtime billing.quote v2 #\w+>, line 1"),
        ("raise RuntimeError('boom')\n", "boom"),
        (
            "from sincpro_framework import DataTransferObject\n",
            "no Feature or ApplicationService",
        ),
        (QUOTE + "\n\nclass Other(Quote):\n    pass\n", "Other, Quote"),
        (
            "from sincpro_framework import Feature\n\n"
            "class Quote(Feature):\n    def execute(self, dto):\n        return None\n",
            "which Command it answers",
        ),
    ],
)
def test_a_broken_source_is_refused_and_the_bus_stays(source: str, reason: str):
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    bus = registry.current

    registry.store.save(RuntimeUseCase("quote", source, version=2))
    with pytest.raises(UseCaseRefused, match=reason) as refused:
        registry.reload()

    assert "quote v2" in str(refused.value)
    assert registry.current is bus
    assert registry.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("113.00")


def test_answering_a_command_of_the_code_needs_replaces():
    registry = _registry()

    registry.store.save(RuntimeUseCase("tax", TAX_WITH_EXEMPTION))
    with pytest.raises(
        UseCaseRefused,
        match=r'ComputeTax answers CommandComputeTax in code — say replaces="tests.runtime_use_cases.billing.ComputeTax"',
    ):
        registry.reload()


def test_a_stored_feature_replaces_the_one_in_code():
    replaced = "tests.runtime_use_cases.billing.ComputeTax"
    registry = _registry(RuntimeUseCase("tax", TAX_WITH_EXEMPTION, replaces=replaced))

    small = registry.current(CommandComputeTax(amount=Decimal("50")), ResponseComputeTax)
    described = features(registry.current)["CommandComputeTax"]

    assert small.tax == Decimal("0")
    assert described.type.__name__ == "ComputeTaxWithExemption"
    assert described.replaces == (replaced,)


def test_replacing_what_does_not_answer_the_command_is_refused():
    registry = _registry()

    registry.store.save(
        RuntimeUseCase("tax", TAX_WITH_EXEMPTION, replaces="billing.features.OldTax")
    )
    with pytest.raises(UseCaseRefused, match="ComputeTax answers CommandComputeTax"):
        registry.reload()


def test_the_bus_the_code_declares_is_never_built_nor_changed():
    registry = _registry(RuntimeUseCase("quote", QUOTE))

    registry.reload()

    assert registry.current is not billing
    assert not billing.was_initialized
    assert billing.handler_of(CommandComputeTax) is ComputeTax


def test_a_registry_leaves_the_modules_of_another_one_alone():
    _registry(RuntimeUseCase("quote", QUOTE)).current
    _registry(RuntimeUseCase("checkout", CHECKOUT)).current

    assert "sincpro_runtime.billing.quote" in sys.modules
    assert "sincpro_runtime.billing.checkout" in sys.modules


def test_check_refuses_a_draft_without_saving_it_or_touching_the_bus():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    bus = registry.current

    with pytest.raises(UseCaseRefused, match="line 1"):
        registry.check(RuntimeUseCase("quote", "class Quote(Feature)\n", version=2))
    registry.check(RuntimeUseCase("quote", QUOTE.replace("1.13", "1.16"), version=2))

    assert registry.current is bus
    assert registry.store.active() == [RuntimeUseCase("quote", QUOTE)]
    assert registry.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("113.00")


def test_a_traceback_shows_the_stored_line_that_failed():
    failing = QUOTE.replace(
        'return ResponseQuote(total=dto.amount * Decimal("1.13"))',
        'raise ValueError("no price list")',
    )
    registry = _registry(RuntimeUseCase("quote", failing, version=3))

    with pytest.raises(ValueError) as raised:
        registry.execute(QUOTE_COMMAND, {"amount": 100})
    shown = "".join(traceback.format_exception(raised.value))

    assert "<runtime billing.quote v3 #" in shown
    assert 'raise ValueError("no price list")' in shown


def test_callers_never_fail_while_generations_are_swapped():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    errors: list[Exception] = []
    stop = threading.Event()

    def call() -> None:
        while not stop.is_set():
            try:
                registry.execute(QUOTE_COMMAND, {"amount": 100})
            except Exception as error:
                errors.append(error)
            stop.wait(0.0005)

    callers = [threading.Thread(target=call) for _ in range(4)]
    for caller in callers:
        caller.start()
    for version in range(2, 12):
        rate = "1.13" if version % 2 else "1.16"
        registry.store.save(RuntimeUseCase("quote", QUOTE.replace("1.13", rate), version))
        registry.reload()
    stop.set()
    for caller in callers:
        caller.join()

    assert errors == []
    assert registry.generation == 11


def test_check_loads_a_draft_where_its_version_in_force_stands():
    registry = _registry(
        RuntimeUseCase("quote", QUOTE), RuntimeUseCase("double", DOUBLE_QUOTE)
    )
    renamed = QUOTE.replace("CommandQuote", "CommandPrice")

    with pytest.raises(UseCaseRefused, match="double v1: cannot import name 'CommandQuote'"):
        registry.check(RuntimeUseCase("quote", renamed, version=2))


def test_a_check_leaves_the_lines_a_traceback_shows_as_they_are():
    failing = QUOTE.replace(
        'return ResponseQuote(total=dto.amount * Decimal("1.13"))',
        'raise ValueError("no price list")',
    )
    registry = _registry(RuntimeUseCase("quote", failing))

    registry.check(RuntimeUseCase("quote", QUOTE))
    with pytest.raises(ValueError) as raised:
        registry.execute(QUOTE_COMMAND, {"amount": 100})

    assert 'raise ValueError("no price list")' in "".join(
        traceback.format_exception(raised.value)
    )


def test_a_feature_cannot_replace_an_application_service():
    registry = _registry(RuntimeUseCase("checkout", CHECKOUT))
    replaced = "sincpro_runtime.billing.checkout.Checkout"

    registry.store.save(RuntimeUseCase("as_feature", FEATURE_FOR_CHECKOUT, replaces=replaced))
    with pytest.raises(UseCaseRefused, match="Checkout is an ApplicationService"):
        registry.reload()


class CountedReads(InMemoryUseCases):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def active(self) -> list[RuntimeUseCase]:
        self.reads += 1
        return super().active()


def test_a_registry_reads_its_store_on_first_use_not_when_it_is_made():
    """Context: made at import, a registry read a table the migrations had not created yet."""
    store = CountedReads()
    store.save(RuntimeUseCase("quote", QUOTE))

    registry = BusRegistry(billing, store)
    assert store.reads == 0

    answered = registry.execute(QUOTE_COMMAND, {"amount": 100})

    assert answered.total == Decimal("113.00")
    assert store.reads == 1 and registry.generation == 1


def test_callers_arriving_together_build_one_first_generation():
    store = CountedReads()
    store.save(RuntimeUseCase("quote", QUOTE))
    registry = BusRegistry(billing, store)
    start = threading.Barrier(6)
    seen: list[Any] = []

    def first_use() -> None:
        start.wait()
        seen.append(registry.current)

    callers = [threading.Thread(target=first_use) for _ in range(6)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()

    assert store.reads == 1 and registry.generation == 1
    assert all(bus is seen[0] for bus in seen)


def test_put_saves_a_use_case_and_swaps_in_the_generation_it_joins():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    before = registry.current

    registry.put(RuntimeUseCase("quote", QUOTE.replace("1.13", "1.16"), version=2))

    assert registry.store.active()[0].version == 2
    assert registry.current is not before and registry.generation == 2
    assert registry.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("116.00")
    assert registry.reload() is False


def test_put_of_what_does_not_load_saves_nothing_and_leaves_the_bus():
    registry = _registry(RuntimeUseCase("quote", QUOTE))
    bus = registry.current

    with pytest.raises(UseCaseRefused, match="quote v2"):
        registry.put(RuntimeUseCase("quote", "class Quote(Feature)\n", version=2))

    assert registry.store.active() == [RuntimeUseCase("quote", QUOTE)]
    assert registry.current is bus
    assert registry.reload() is False


def test_put_retires_a_use_case_saved_inactive():
    registry = _registry(RuntimeUseCase("quote", QUOTE))

    registry.put(RuntimeUseCase("quote", QUOTE, version=2, active=False))

    assert QUOTE_COMMAND not in registry.current.dto_registry


def test_check_all_names_every_stored_use_case_the_code_no_longer_loads():
    """Context: a stored source imports the code; a refactor of the code breaks it silently until
    the next reload — this is the CI check, as `migrations check` is for the schema."""
    store = InMemoryUseCases()
    store.save(RuntimeUseCase("quote", QUOTE))
    store.save(RuntimeUseCase("broken", "class Broken(Feature)\n"))
    store.save(RuntimeUseCase("tax", TAX_WITH_EXEMPTION))
    registry = BusRegistry(billing, store)

    refusals = registry.check_all()

    assert [str(refusal).split(":")[0] for refusal in refusals] == ["broken v1", "tax v1"]
    assert registry.generation == 0


def test_check_all_answers_nothing_when_every_stored_use_case_loads():
    registry = _registry(
        RuntimeUseCase("quote", QUOTE), RuntimeUseCase("double", DOUBLE_QUOTE)
    )

    assert registry.check_all() == []
    assert registry.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("113.00")


def test_the_registry_says_which_versions_answer_even_when_the_store_moved_on():
    quote_v1 = RuntimeUseCase("quote", QUOTE)
    registry = _registry(quote_v1, RuntimeUseCase("checkout", CHECKOUT))
    assert registry.in_force == ()

    registry.current
    assert [(one.name, one.version) for one in registry.in_force] == [
        ("quote", 1),
        ("checkout", 1),
    ]

    registry.store.save(RuntimeUseCase("quote", "raise RuntimeError('boom')\n", version=2))
    with pytest.raises(UseCaseRefused):
        registry.reload()

    assert registry.in_force[0] == quote_v1
