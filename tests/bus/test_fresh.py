"""`UseFramework.fresh()`: a new bus of the same context, not built, with everything registered on
this one — what a new generation of the bus starts from."""

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandPrice(DataTransferObject):
    sku: str


class ResponsePrice(DataTransferObject):
    price: int


class CommandCart(DataTransferObject):
    skus: list[str]


class ResponseCart(DataTransferObject):
    total: int


class Prices:
    def __init__(self) -> None:
        self.table = {"chair": 40, "desk": 100}


class ExpectedError(Exception):
    pass


def _shop() -> tuple[UseFramework, list[str]]:
    shop = UseFramework("shop", log_after_execution=False, hide_in_logs=["TOKEN"])
    shop.add_dependency("prices", Prices())
    seen: list[str] = []

    @shop.feature(CommandPrice)
    class PriceOf(Feature):
        def execute(self, dto: CommandPrice) -> ResponsePrice:
            if dto.sku == "broken":
                raise ExpectedError("no price")
            return ResponsePrice(price=self.prices.table[dto.sku])

    @shop.app_service(CommandCart)
    class Cart(ApplicationService):
        def execute(self, dto: CommandCart) -> ResponseCart:
            prices = [
                self.feature_bus.execute(CommandPrice(sku=sku), ResponsePrice)
                for sku in dto.skus
            ]
            return ResponseCart(total=sum(price.price for price in prices))

    @shop.interceptor()
    def outer(dto, call_next):
        seen.append(f"outer {type(dto).__name__}")
        return call_next(dto)

    @shop.interceptor(CommandPrice)
    def inner(dto, call_next):
        seen.append(f"inner {dto.sku}")
        return call_next(dto)

    shop.add_feature_error_handler(lambda error: ResponsePrice(price=-1))
    shop.ignore_sentry_exceptions(ExpectedError)
    return shop, seen


def test_a_fresh_bus_answers_what_this_one_does_and_is_not_built():
    shop, _ = _shop()

    fresh = shop.fresh()

    assert fresh is not shop and not fresh.was_initialized
    assert fresh(CommandCart(skus=["chair", "desk"]), ResponseCart).total == 140
    assert fresh.deps.prices is shop.deps.prices


def test_interceptors_and_error_handlers_come_along_in_their_order():
    shop, seen = _shop()

    fresh = shop.fresh()
    answered = fresh(CommandPrice(sku="broken"), ResponsePrice)

    assert answered.price == -1
    assert seen == ["outer CommandPrice", "inner broken"]


def test_the_settings_and_the_observability_are_this_ones():
    shop, _ = _shop()

    fresh = shop.fresh()

    assert fresh.name == "shop"
    assert fresh.log_after_execution is False
    assert fresh.observability is shop.observability
    assert ExpectedError in fresh.observability.ignored_errors
    fresh.build_root_bus()
    assert fresh.bus is not None
    assert fresh.bus.feature_bus.observability is shop.observability
    assert fresh.bus.app_service_bus.observability is shop.observability


def test_a_replacement_is_registered_again_in_place_of_what_it_replaced():
    shop, _ = _shop()
    replaced = shop.handler_of(CommandPrice)

    @shop.feature(CommandPrice, replaces=replaced)
    class FlatPrice(Feature):
        def execute(self, dto: CommandPrice) -> ResponsePrice:
            return ResponsePrice(price=10)

    fresh = shop.fresh()

    assert fresh.handler_of(CommandPrice) is FlatPrice
    assert fresh(CommandCart(skus=["chair", "desk"]), ResponseCart).total == 20


def test_a_fresh_bus_of_a_built_one_leaves_it_as_it_is():
    shop, _ = _shop()
    assert shop(CommandPrice(sku="chair"), ResponsePrice).price == 40

    fresh = shop.fresh()

    @fresh.app_service(CommandCart, replaces=fresh.handler_of(CommandCart))
    class EmptyCart(ApplicationService):
        def execute(self, dto: CommandCart) -> ResponseCart:
            return ResponseCart(total=0)

    assert fresh(CommandCart(skus=["chair"]), ResponseCart).total == 0
    assert shop(CommandCart(skus=["chair"]), ResponseCart).total == 40
    assert shop.fresh()(CommandCart(skus=["chair"]), ResponseCart).total == 40


def test_a_fresh_bus_of_a_fresh_one_has_everything_too():
    shop, _ = _shop()

    assert shop.fresh().fresh()(CommandCart(skus=["desk"]), ResponseCart).total == 100
