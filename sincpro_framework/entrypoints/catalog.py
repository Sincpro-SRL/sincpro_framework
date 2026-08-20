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
from sincpro_framework.introspection import inspector
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


class PackedFeatureOrAppService(DataTransferObject):
    """One Feature or ApplicationService, packed for a JSON-speaking driving adapter.

    `layer` is "features" or "app_services" — the bus's own vocabulary; also names
    the RPC method namespace (`{alias}.{layer}.{DtoName}`) and the MCP/OpenRPC tag.
    `json_schema` is the input DTO's schema, computed once here — hosts must not
    recompute it. `run` is a bound callable, not JSON; DataTransferObject allows
    arbitrary types so it can travel alongside the JSON-safe fields.
    """

    name: str
    layer: Layer
    description: str
    dto: type[DataTransferObject]
    json_schema: dict[str, Any]
    run: RunFn


class Catalog:
    """One UseFramework instance as a filtered list of Features/ApplicationServices.

    `include`/`exclude`/`wrap` narrow the DTO surface or decorate one `run` (auth,
    audit, extra logging). Hosts (MCP, JSON-RPC, REST, CLI) call
    `build_scalar_feature_and_app_services(filter_binaries_schema=True)` and map
    the `PackedFeatureOrAppService` list to their wire.
    """

    def __init__(self, framework_instance: UseFramework):

        if not framework_instance.was_initialized:
            framework_instance.build_root_bus()

        self.framework_instance = framework_instance
        self._include: set[str] | None = None
        self._exclude: set[str] = set()
        self._wrappers: dict[str, Wrapper] = {}

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

        1. Skip names outside include, or listed in exclude.
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
                    run=run,
                )
            )
        return result

    def include(self, *dtos: type | str) -> Self:
        self._include = self._names(*dtos)
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self._exclude = self._names(*dtos)
        return self

    def wrap(self, dto: type | str, wrapper: Wrapper) -> Self:
        key = dto if isinstance(dto, str) else dto.__name__
        self._wrappers[key] = wrapper
        return self

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
        2. Bind Features then ApplicationServices.
        3. Final: drop binary-schema entries when filter_binaries_schema is True.
        """
        if not self.framework_instance.was_initialized:
            self.framework_instance.build_root_bus()

        features = self._convert_to_scalar_use_case(
            inspector.features(self.framework_instance), Layer.FEATURES
        )

        app_services = self._convert_to_scalar_use_case(
            inspector.app_services(self.framework_instance), Layer.APP_SERVICES
        )

        entries = [
            *features,
            *app_services,
        ]

        if not filter_binaries_schema:
            return entries

        result: list[PackedFeatureOrAppService] = []
        for entry in entries:
            if json_utils.is_binary_free(entry.json_schema, entry.dto):
                result.append(entry)
                continue
            logger.warning("Skipping non-JSON Feature/ApplicationService [%s]", entry.name)
        return result
