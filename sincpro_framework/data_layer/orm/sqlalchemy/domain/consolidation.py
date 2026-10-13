"""A class that reads and writes several mapped entities as one flat record."""


class Consolidated:
    """The entities one flat class is made of: the core, then each extension.

        @dataclass
        class MoveConsolidated(Entity):          # a module that knows both extensions
            number: str = ""
            route: str = ""
            cufd: str = ""

        map_aggregates(registry, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})

    The class does not inherit the entities, so extensions that are siblings can be one object
    here. A record without a row of an extension reads it as the field's default; saving writes
    every table, the core's first.
    """

    def __init__(self, core: type, *extensions: type) -> None:
        self.core = core
        self.extensions = extensions

    @property
    def entities(self) -> tuple[type, ...]:
        return (self.core, *self.extensions)
