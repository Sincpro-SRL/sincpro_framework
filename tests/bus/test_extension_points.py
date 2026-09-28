"""Interceptors and error handlers extended the way Features and repository hooks are: by
reference, `replaces=` in place, `without_*` to switch one off, and one order for all of them —
`before`/`after`, then `sequence` (lower first), then the order they were registered. What works,
only not as said, is a warning; only what cannot work is refused."""

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.exceptions import BusAlreadyBuilt
from sincpro_framework.introspection import features


class CommandPay(DataTransferObject):
    amount: int


class ResponsePay(DataTransferObject):
    paid: int


class CommandRefund(DataTransferObject):
    amount: int


class PaymentFailed(Exception):
    pass


def _payments(seen: list[str]) -> UseFramework:
    payments = UseFramework("payments-extension", log_after_execution=False)

    @payments.feature(CommandPay)
    class Pay(Feature):
        def execute(self, dto: CommandPay) -> ResponsePay:
            if dto.amount < 0:
                raise PaymentFailed("negative")
            seen.append("pay")
            return ResponsePay(paid=dto.amount)

    @payments.feature(CommandRefund)
    class Refund(Feature):
        def execute(self, dto: CommandRefund) -> None:
            seen.append("refund")

    return payments


def _named(seen: list[str], name: str):
    def interceptor(dto, call_next):
        seen.append(name)
        return call_next(dto)

    interceptor.__qualname__ = name
    return interceptor


def test_interceptors_run_outermost_first_by_before_after_sequence_then_registration():
    seen: list[str] = []
    payments = _payments(seen)
    audit, credit, fraud = (
        _named(seen, "audit"),
        _named(seen, "credit"),
        _named(seen, "fraud"),
    )
    payments.interceptor(CommandPay, sequence=20)(audit)
    payments.interceptor(CommandPay)(credit)
    payments.interceptor(CommandPay, before=(credit,))(fraud)

    payments(CommandPay(amount=10), ResponsePay)

    assert seen == ["fraud", "credit", "audit", "pay"]


def test_a_replacing_interceptor_runs_in_the_place_of_the_one_it_replaces():
    seen: list[str] = []
    payments = _payments(seen)
    audit, credit, credit_v2 = (
        _named(seen, "audit"),
        _named(seen, "credit"),
        _named(seen, "credit v2"),
    )
    payments.interceptor(CommandPay)(credit)
    payments.interceptor(CommandPay)(audit)
    payments.interceptor(CommandPay, replaces=credit)(credit_v2)

    payments(CommandPay(amount=10), ResponsePay)

    assert seen == ["credit v2", "audit", "pay"]
    assert features(payments)["CommandPay"].interceptors == (
        f"{__name__}.credit v2",
        f"{__name__}.audit",
    )


def _warnings(logs: list) -> list[str]:
    return [line["event"] for line in logs if line["log_level"] == "warning"]


def test_a_replacing_interceptor_that_wraps_other_commands_wraps_them_with_a_warning():
    seen: list[str] = []
    payments = _payments(seen)
    credit, credit_v2 = _named(seen, "credit"), _named(seen, "credit v2")
    payments.interceptor(CommandPay)(credit)
    payments.interceptor(CommandRefund, replaces=credit)(credit_v2)

    with capture_logs() as logs:
        payments(CommandPay(amount=10), ResponsePay)
    payments(CommandRefund(amount=1))

    assert seen == ["pay", "credit v2", "refund"]
    assert "wraps CommandRefund" in _warnings(logs)[0]


def test_replacing_an_interceptor_that_is_not_registered_runs_it_with_a_warning():
    seen: list[str] = []
    payments = _payments(seen)
    payments.interceptor(CommandPay, replaces=_named(seen, "ghost"))(_named(seen, "credit"))

    with capture_logs() as logs:
        payments(CommandPay(amount=10), ResponsePay)

    assert seen == ["credit", "pay"]
    assert "not registered" in _warnings(logs)[0]


def test_an_interceptor_switched_off_does_not_run():
    seen: list[str] = []
    payments = _payments(seen)
    audit, credit = _named(seen, "audit"), _named(seen, "credit")
    payments.interceptor(CommandPay)(audit)
    payments.interceptor(CommandPay)(credit)
    payments.without_interceptor(audit)

    payments(CommandPay(amount=10), ResponsePay)

    assert seen == ["credit", "pay"]


def test_switching_off_an_interceptor_after_the_bus_is_built_is_refused():
    seen: list[str] = []
    payments = _payments(seen)
    audit = _named(seen, "audit")
    payments.interceptor(CommandPay)(audit)
    payments(CommandPay(amount=1), ResponsePay)

    with pytest.raises(BusAlreadyBuilt):
        payments.without_interceptor(audit)


def test_a_fresh_bus_keeps_the_replacements_and_what_was_switched_off():
    seen: list[str] = []
    payments = _payments(seen)
    audit, credit, credit_v2 = (
        _named(seen, "audit"),
        _named(seen, "credit"),
        _named(seen, "credit v2"),
    )
    payments.interceptor(CommandPay)(audit)
    payments.interceptor(CommandPay)(credit)
    payments.interceptor(CommandPay, replaces=credit)(credit_v2)
    payments.without_interceptor(audit)

    payments.fresh()(CommandPay(amount=10), ResponsePay)

    assert seen == ["credit v2", "pay"]


def _handler(seen: list[str], name: str, answer: bool = False):
    def handler(error: Exception):
        seen.append(name)
        if answer:
            return ResponsePay(paid=0)
        raise error

    handler.__qualname__ = name
    return handler


def test_error_handlers_run_in_the_same_order_and_the_last_answer_wins_the_call():
    seen: list[str] = []
    payments = _payments(seen)
    payments.add_feature_error_handler(_handler(seen, "base", answer=True), sequence=90)
    payments.add_feature_error_handler(_handler(seen, "log"))

    answered = payments(CommandPay(amount=-1), ResponsePay)

    assert seen == ["log", "base"] and answered == ResponsePay(paid=0)


def test_an_error_handler_is_replaced_in_place_and_switched_off():
    seen: list[str] = []
    payments = _payments(seen)
    log, notify, notify_v2 = (
        _handler(seen, "log"),
        _handler(seen, "notify"),
        _handler(seen, "notify v2"),
    )
    base = _handler(seen, "base", answer=True)
    payments.add_feature_error_handler(log)
    payments.add_feature_error_handler(notify)
    payments.add_feature_error_handler(base)
    payments.add_feature_error_handler(notify_v2, replaces=notify)
    payments.without_error_handler(log)

    payments(CommandPay(amount=-1), ResponsePay)

    assert seen == ["notify v2", "base"]


def test_switching_off_an_error_handler_that_is_not_registered_is_a_warning_at_build():
    payments = _payments([])
    payments.without_error_handler(_handler([], "ghost"))

    with capture_logs() as logs:
        payments(CommandPay(amount=10), ResponsePay)

    assert "not registered" in _warnings(logs)[0]


def test_an_error_handler_switched_off_before_it_is_registered_stays_off():
    """A project may switch off a core's handler before the core registers it."""
    seen: list[str] = []
    payments = _payments(seen)
    log, base = _handler(seen, "log"), _handler(seen, "base", answer=True)
    payments.without_error_handler(log)
    payments.add_feature_error_handler(log)
    payments.add_feature_error_handler(base)

    payments(CommandPay(amount=-1), ResponsePay)

    assert seen == ["base"]
