# Recipe: import records from a file — `Import` + `EntityImports`

Use it when a service must load many records of one entity from CSV, JSON, JSON Lines or an Excel
sheet — a migration from Odoo, a spreadsheet a person fills in, a nightly mirror — with references
to other records (`partner.country_id`) and children (`invoice.lines`). Each record is saved as a
save saves it: hooks, `@check`, children with their root. Full page: `docs/persistence/importing.md`.

## 1. Declare the DTO and one Feature

```python
from sincpro_framework.ddd import EntityImports, Import, ImportFormat, ResponseImport

class CommandImportPartners(Import[Partner, ResponseImport]):
    origin: str = "odoo"                    # a project fixes its defaults by redeclaring fields

@sales.feature(CommandImportPartners)
class PartnerImports(EntityImports[Partner]):   # an EntityReads too
    pass

sales(CommandImportPartners(content=data, format=ImportFormat.CSV), ResponseImport)
# -> ResponseImport(created=…, updated=…, unchanged=…, skipped=[FieldError…])
```

## 2. How the file names records

| Header | Means |
|---|---|
| `name` | the field |
| `country_id` | the Country found by its `DEFAULT_GET_ID` (declare a natural key: `return "code"`) |
| `country_id/@key` | the Country whose external key is this, in the import's `origin` |
| `@key` / `id` | this record's own external key / identity |
| `lines/quantity` | a child; a row with blank own cells continues the record above (one level in flat files; JSON nests any depth) |
| `labels` | a many-to-many: several keys in one cell, comma-separated |

A reference names a record **stored, or earlier in the file**. JSON names one by `"BO"`,
`{"code": "BO"}`, `{"@key": …}` or `{"id": …}`.

## 3. External keys (`@key`)

```python
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import external_key_table
map_aggregates(registry, {ExternalKey: external_key_table("sales_external_keys", metadata)})
```

One table per bounded context, saved in the import's unit of work. Two contexts sharing a mapper
registry map a subclass and set `external_keys = SalesExternalKey` on the Feature. **Never derive
the identity from the outside key** (no `uuid5`): `EntityID` is a UUID v7 and a foreign name changes.

## 4. Transaction and failures

- One unit of work. `skip_invalid=False` (default): an invalid record refuses the whole file with
  `ImportRefused` (422), every failure a `FieldError` pointing at its cell
  (`#/content/12/country_id`). `skip_invalid=True`: invalid records go to `skipped`, the rest is
  written.
- `dry_run=True` judges the whole file and answers a `Preview` (or refuses the same way).
- `mode=ImportMode.CREATE` / `UPDATE` refuses what exists / what does not.

## 5. Extending

Override a step on the Feature, calling `super()`: `rows(dto)` (a format of its own),
`records(dto, rows)`, `resolve(dto, batch, keys)`, `identities(dto, kind, by, keys)` (how a key is
looked up), `build(dto, batch)` (a rule of this import alone), `write(dto, batch)`.

## Mistakes

- Writing a Feature by hand that loops `save` per row and looks each reference up per row: use
  `EntityImports` — it looks each key up once per import and saves in batches.
- Naming a reference with the relation field (`country`) instead of its key field (`country_id`).
- Expecting a forward reference (a parent below its child) to resolve: put the parent first.
- Using `upsert` for an import that must judge rules or write children: `upsert` skips both, and
  measured slower than `save` in batches.
