"""Records of one entity from a file — CSV, JSON or an Excel sheet —, saved as a save saves them,
with their children and their references to other records (`imports`); the name another system
gives a record, kept beside it (`external_key`).

    class CommandImportPartners(Import[Partner, ResponseImport]): ...

    @sales.feature(CommandImportPartners)
    class PartnerImports(EntityImports[Partner]): ...

    sales(CommandImportPartners(content=data, format=ImportFormat.CSV), ResponseImport)

How a file is read (`reading`, `excel`) and how a row becomes a record (`shaping`) are imported
from their own files, by whoever overrides a step.
"""

from sincpro_framework.ddd.importing.external_key import ExternalKey
from sincpro_framework.ddd.importing.imports import (
    EntityImports,
    Import,
    ImportMode,
    ImportOutcome,
    ImportRecord,
    ResponseImport,
)
from sincpro_framework.ddd.importing.reading import ImportFormat, ImportRow

__all__ = [
    "EntityImports",
    "ExternalKey",
    "Import",
    "ImportFormat",
    "ImportMode",
    "ImportOutcome",
    "ImportRecord",
    "ImportRow",
    "ResponseImport",
]
