from typing import Any, Callable, List, Protocol


class Middleware(Protocol):
    """Simple middleware protocol - just a callable that processes DTOs"""

    def __call__(self, dto: Any) -> Any:
        """
        Process the DTO and return the (possibly modified) DTO.
        Can raise exceptions if validation fails or requirements aren't met.

        Args:
            dto: The data transfer object to process

        Returns:
            The processed DTO (may be the same object or a modified version)

        Raises:
            Any exception if processing fails or validation doesn't pass
        """
        ...


def _refuse_a_type_that_lost_fields(processed: Any, original: type) -> None:
    """Refuses a middleware that answered with something the original type is not.

    Middleware may enrich a DTO into a wider one — that is what the class is rewritten for, so
    the registry still routes on the type the Feature was registered with. It works because the
    wider type *has* everything the original declared.

    Answer with an unrelated type and the rewrite produces an object that says it is the
    original and has none of its fields. The Feature then fails on `dto.whatever`, inside
    business logic, with nothing pointing back at the middleware that caused it.
    """
    declared = getattr(original, "model_fields", None)
    if not declared:
        return
    missing = [name for name in declared if not hasattr(processed, name)]
    if missing:
        raise TypeError(
            f"middleware answered with {type(processed).__name__}, which is missing "
            f"{', '.join(sorted(missing))} — a middleware may enrich {original.__name__} into "
            "something wider, but what it answers has to still be one"
        )


class MiddlewarePipeline:
    """Simple pipeline that executes middleware functions in sequence"""

    def __init__(self):
        self.middlewares: List[Middleware] = []

    def add_middleware(self, middleware: Middleware):
        """Add middleware function to pipeline"""
        self.middlewares.append(middleware)

    def execute(self, dto: Any, executor: Callable, **kwargs) -> Any:
        """
        Execute the middleware pipeline and then the main executor.

        Preserves registry compatibility by monkey patching transformed DTOs
        to maintain the original DTO class for registry lookup.
        """
        # Store original class for registry compatibility
        original_dto_class = dto.__class__

        processed_dto = dto
        for middleware in self.middlewares:
            processed_dto = middleware(processed_dto)

        # If DTO type changed, monkey patch to preserve registry compatibility
        if processed_dto.__class__ != original_dto_class:
            _refuse_a_type_that_lost_fields(processed_dto, original_dto_class)
            processed_dto.__class__ = original_dto_class

        # Execute main operation with processed DTO
        return executor(processed_dto, **kwargs)
