"""Shared catalog for driving adapters (MCP, JSON-RPC, later REST/CLI).

Packs what `sincpro_framework.introspection` already described — Features,
ApplicationServices, DTOs — for a JSON-speaking host. Hosts only add a wire:
FastMCP tools, JSON-RPC methods, FastAPI routes, CLI commands. Do not put
FastMCP, Starlette, or argparse types here.

    catalog = Catalog(billing).exclude(CommandReconcile).wrap("CommandIssue", audited)
    [one.name for one in catalog.get_scalar_use_cases(filter_binaries_schema=True)]
    ->  ["CommandIssue", "QueryInvoice"]
    catalog.get_scalar_use_cases()[0].run({"invoice_id": 7})  ->  {"status": "issued"}
"""

from collections.abc import Mapping
from typing import Any, Self

from sincpro_framework.entrypoints.domain.layers import Layer, RunFn, Wrapper
from sincpro_framework.entrypoints.entrypoint.internal import is_internal
from sincpro_framework.entrypoints.infrastructure import json_schema
from sincpro_framework.entrypoints.services import scalar_executor
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
        """Hold `framework_instance` with nothing narrowed — every use case it answers.

        Context: the bus is built here, so a catalog made before the first call already sees
        every Feature and ApplicationService registered; its revision is noted so a later
        registration on the bus lets the packed answers go.

            Catalog(billing)                    ->  every use case of billing, none wrapped
            Catalog(billing).revision           ->  billing.revision — no change of its own

        1. The bus's root built.
        2. Nothing included (every use case), nothing excluded, nothing wrapped.
        3. Nothing packed yet; the bus's revision noted as the one packed from.
        Final: no change of its own — `revision` is the bus's.
        """

        framework_instance.build_root_bus()

        self.framework_instance = framework_instance
        self._include: set[str] | None = None
        self._exclude: set[str] = set()
        self._wrappers: dict[str, Wrapper] = {}
        self._packed: dict[bool, list[PackedFeatureOrAppService]] = {}
        """What `get_scalar_use_cases` answered, by its filter — the schemas computed once;
        `include` / `exclude` / `wrap`, or something registered on the bus, let it go."""
        self._packed_from = framework_instance.revision
        """The bus's revision the packed answers were read from."""
        self._changes = 0
        """Bumped by every include/exclude/wrap."""

    @property
    def revision(self) -> int:
        """Grows with every include/exclude/wrap and with every registration on the bus after it
        was built — what a gateway's resolved surface checks."""
        return self._changes + self.framework_instance.revision

    @staticmethod
    def _names(*dtos: type | str) -> set[str]:
        """Each Command by the name the catalog keys it under — a class by its `__name__`."""
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
                    json_schema=json_schema.dto_json_schema(metadata.dto),
                    response_json_schema=(
                        json_schema.json_schema_for(metadata.response)
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
        """Publish only these use cases — by Command or its name; a second call replaces the
        first, it does not add to it.

            Catalog(billing).include(CommandIssue, "QueryInvoice")
        """
        self._include = self._names(*dtos)
        self._packed.clear()
        self._changes += 1
        return self

    def exclude(self, *dtos: type | str) -> Self:
        """Leave these use cases out — by Command or its name; it wins over `include`, and a
        second call replaces the first.

            Catalog(billing).exclude(CommandReconcile)
        """
        self._exclude = self._names(*dtos)
        self._packed.clear()
        self._changes += 1
        return self

    def wrap(self, dto: type | str, wrapper: Wrapper) -> Self:
        """Decorate one use case's `run` — auth, audit, extra logging — on this catalog only;
        the bus and every other gateway run it as it is. One wrapper per Command: the last
        wins.

            Catalog(billing).wrap(CommandIssue, lambda run: lambda payload: run(payload))
        """
        key = dto if isinstance(dto, str) else dto.__name__
        self._wrappers[key] = wrapper
        self._packed.clear()
        self._changes += 1
        return self

    def _json_safe(
        self, entries: list[PackedFeatureOrAppService]
    ) -> list[PackedFeatureOrAppService]:
        """Context: derived from the packed entries, so a schema is computed once whichever
        view a wire asks for first."""
        result: list[PackedFeatureOrAppService] = []
        for entry in entries:
            if json_schema.is_binary_free(entry.json_schema, entry.dto):
                result.append(entry)
                continue
            logger.warning("Skipping non-JSON Feature/ApplicationService [%s]", entry.name)
        return result

    def _packed_use_cases(
        self, filter_binaries_schema: bool
    ) -> list[PackedFeatureOrAppService]:
        """Pack every use case afresh — what `get_scalar_use_cases` keeps until something
        changes.

        1. JSON-safe asked: the full answer, read through `get_scalar_use_cases` so it is
           packed once, filtered of what carries bytes.
        2. Build the root bus and read what introspection describes, Features and
           ApplicationServices.
        3. Warn about a name in include/exclude/wrap no use case has.
        Final: Features first, then ApplicationServices, each narrowed and bound.
        """
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

    def get_scalar_use_cases(
        self, filter_binaries_schema: bool = False
    ) -> list[PackedFeatureOrAppService]:
        """Every Feature and ApplicationService bound to a Scalar-callable `run`,
        already filtered by include/exclude.

        filter_binaries_schema=True additionally drops DTOs that cannot travel
        as JSON (a `bytes` field, `format: binary|byte` in the schema) — skipped
        with a warning. Still callable in-process via `framework(dto)`; just not
        exposed on a JSON wire.

            Catalog(billing).get_scalar_use_cases()
            ->  [PackedFeatureOrAppService(name="CommandIssueInvoice", ...), ...]
            Catalog(billing).get_scalar_use_cases(filter_binaries_schema=True)
            ->  the same, without CommandUploadScan (a `bytes` field)

        1. Build the root bus if the instance was never initialized.
        2. Warn about a name in include/exclude/wrap that no use case has.
        3. Bind Features then ApplicationServices.
        4. Final: drop binary-schema entries when filter_binaries_schema is True — the answer
           kept, so a wire that asks on every request computes it once.
        """
        if self._packed_from != self.framework_instance.revision:
            self._packed.clear()
            self._packed_from = self.framework_instance.revision
        kept = self._packed.get(filter_binaries_schema)
        if kept is not None:
            return list(kept)
        packed = self._packed[filter_binaries_schema] = self._packed_use_cases(
            filter_binaries_schema
        )
        return list(packed)
