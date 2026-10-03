"""Shared catalog for driving adapters (MCP, JSON-RPC, later REST/CLI).

Packs what `sincpro_framework.introspection` already described — Features,
ApplicationServices, DTOs — for a JSON-speaking host. Hosts only add a wire:
FastMCP tools, JSON-RPC methods, FastAPI routes, CLI commands. Do not put
FastMCP, Starlette, or argparse types here.
"""

from collections.abc import Mapping
from typing import Any, Self

from sincpro_framework.entrypoints import json_utils, scalar_executor
from sincpro_framework.entrypoints.const import Layer, RunFn, Wrapper
from sincpro_framework.entrypoints.exposure import is_internal
from sincpro_framework.introspection import inspector
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


class PackedFeatureOrAppService(DataTransferObject):
    """One Feature or ApplicationService, packed for a JSON-speaking driving adapter.

    `layer` is "features" or "app_services" — the bus's own vocabulary; also names
    the RPC method namespace (`{alias}.{layer}.{DtoName}`) and the MCP/OpenRPC tag.
    `json_schema` is the input DTO's schema and `response_json_schema` the declared
    response's, both computed once here — hosts must not recompute them.
    `response_json_schema` is None when `execute` declares no return type: a wire
    then publishes an open object rather than a shape nobody promised.
    `run` is a bound callable, not JSON; DataTransferObject allows arbitrary types
    so it can travel alongside the JSON-safe fields.
    """

    name: str
    layer: Layer
    description: str
    dto: type
    json_schema: dict[str, Any]
    response_json_schema: dict[str, Any] | None
    run: RunFn
    handler: type | None = None
    """The Feature or ApplicationService answering `dto` — what exposure reads its bindings,
    its access and its stages from."""
    response: Any = None
    """What `execute` declares it answers, as an annotation — `None` for nothing declared."""


class Catalog:
    """One UseFramework instance as a filtered list of Features/ApplicationServices.

    `include`/`exclude`/`wrap` narrow the DTO surface or decorate one `run` (auth,
    audit, extra logging). Hosts (MCP, JSON-RPC, REST, CLI) call
    `get_scalar_use_cases(filter_binaries_schema=True)` and map
    the `PackedFeatureOrAppService` list to their wire.
    """

    def __init__(self, framework_instance: UseFramework):

        framework_instance.build_root_bus()

        self.framework_instance = framework_instance
        self._include: set[str] | None = None
        self._exclude: set[str] = set()
        self._wrappers: dict[str, Wrapper] = {}
        self._packed: dict[bool, list[PackedFeatureOrAppService]] = {}
        self.revision = 0
        """Bumped by every include/exclude/wrap — what a gateway's resolved surface checks."""
        """What `get_scalar_use_cases` answered, by its filter — a built bus's handlers do not
        change, so the schemas are computed once; `include` / `exclude` / `wrap` let it go."""

    @staticmethod
    def _names(*dtos: type | str) -> set[str]:
        return {dto if isinstance(dto, str) else dto.__name__ for dto in dtos}

    def _convert_to_scalar_use_case(
        self,
        metadata_map: Mapping[str, inspector.FeatureOrAppServiceMetadata],
        layer: Layer,
    ) -> list[PackedFeatureOrAppService]:
        """Turn one layer's described metadata (introspection.FeatureOrAppServiceMetadata)
        into PackedFeatureOrAppService.

        1. Skip names outside include, listed in exclude, or marked `internal`.
        2. Bind execute to framework(dto).
            2.1 If a wrapper exists for this DTO name, wrap the bound run.
        3. Final: a PackedFeatureOrAppService carrying the metadata's description and
           its JSON schema.
        """
        result: list[PackedFeatureOrAppService] = []
        for name, metadata in metadata_map.items():
            if self._include is not None and name not in self._include:
                continue

            if name in self._exclude:
                continue

            if is_internal(metadata.dto, self.framework_instance.handler_of(metadata.dto)):
                continue

            run: RunFn = scalar_executor.extract_executor_fn(
                self.framework_instance, metadata.dto
            )
            wrapper = self._wrappers.get(name)

            if wrapper is not None:
                run = wrapper(run)

            result.append(
                PackedFeatureOrAppService(
                    name=name,
                    layer=layer,
                    description=metadata.description,
                    dto=metadata.dto,
                    json_schema=json_utils.dto_json_schema(metadata.dto),
                    response_json_schema=(
                        json_utils.json_schema_for(metadata.response)
                        if metadata.response is not None
                        else None
                    ),
                    run=run,
                    handler=metadata.type,
                    response=metadata.response,
                )
            )
        return result

    def _warn_unknown_names(self, known: set[str]) -> None:
        """A name in include/exclude/wrap that no use case has is most likely a typo — in
        `exclude` it would leave exposed what it meant to hide, in `wrap` serve a use case
        without its wrapper — so it is said loudly, and the catalog is served as asked."""
        named = (self._include or set()) | self._exclude | set(self._wrappers)
        unknown = sorted(named - known)
        if unknown:
            logger.warning(
                f"{', '.join(unknown)}: no Feature or ApplicationService of "
                f"'{self.framework_instance.name}' answers it, so include/exclude/wrap does "
                f"nothing with it — it answers {', '.join(sorted(known))}"
            )

    def include(self, *dtos: type | str) -> Self:
        self._include = self._names(*dtos)
        self._packed.clear()
        self.revision += 1
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self._exclude = self._names(*dtos)
        self._packed.clear()
        self.revision += 1
        return self

    def wrap(self, dto: type | str, wrapper: Wrapper) -> Self:
        key = dto if isinstance(dto, str) else dto.__name__
        self._wrappers[key] = wrapper
        self._packed.clear()
        self.revision += 1
        return self

    def _packed_use_cases(
        self, filter_binaries_schema: bool
    ) -> list[PackedFeatureOrAppService]:
        if filter_binaries_schema:
            return self._json_safe(self.get_scalar_use_cases())
        self.framework_instance.build_root_bus()

        described_features = inspector.features(self.framework_instance)
        described_app_services = inspector.app_services(self.framework_instance)
        self._warn_unknown_names(set(described_features) | set(described_app_services))
        features = self._convert_to_scalar_use_case(described_features, Layer.FEATURES)
        app_services = self._convert_to_scalar_use_case(
            described_app_services, Layer.APP_SERVICES
        )

        return [
            *features,
            *app_services,
        ]

    def _json_safe(
        self, entries: list[PackedFeatureOrAppService]
    ) -> list[PackedFeatureOrAppService]:
        """Context: derived from the packed entries, so a schema is computed once whichever
        view a wire asks for first."""
        result: list[PackedFeatureOrAppService] = []
        for entry in entries:
            if json_utils.is_binary_free(entry.json_schema, entry.dto):
                result.append(entry)
                continue
            logger.warning("Skipping non-JSON Feature/ApplicationService [%s]", entry.name)
        return result

    def get_scalar_use_cases(
        self, filter_binaries_schema: bool = False
    ) -> list[PackedFeatureOrAppService]:
        """Every Feature and ApplicationService bound to a Scalar-callable `run`,
        already filtered by include/exclude.

        filter_binaries_schema=True additionally drops DTOs that cannot travel
        as JSON (a `bytes` field, `format: binary|byte` in the schema) — skipped
        with a warning. Still callable in-process via `framework(dto)`; just not
        exposed on a JSON wire.

        1. Build the root bus if the instance was never initialized.
        2. Warn about a name in include/exclude/wrap that no use case has.
        3. Bind Features then ApplicationServices.
        4. Final: drop binary-schema entries when filter_binaries_schema is True — the answer
           kept, so a wire that asks on every request computes it once.
        """
        kept = self._packed.get(filter_binaries_schema)
        if kept is not None:
            return list(kept)
        packed = self._packed[filter_binaries_schema] = self._packed_use_cases(
            filter_binaries_schema
        )
        return list(packed)
