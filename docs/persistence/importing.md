# Importing: records from a file, with their references

A business moves into a system with spreadsheets: the partners from the old ERP, the chart of
accounts, the opening invoices. `Import` takes a file (CSV, JSON, JSON Lines or an Excel sheet)
and stores its records the way a save stores them. Every rule is judged, every child is written
with its root, and every reference becomes the identity of the record it names. The file is read
once, a batch at a time, inside one unit of work: the size of the file decides how long the import
takes, never how much memory it holds. The standard library reads every format; nothing is an
extra.

Nothing here is required. A Feature written by hand with `repository.search` and
`repository.save` does the same; `EntityImports` is that Feature, written once.

## The shape

```python
from dataclasses import dataclass

from sincpro_framework.ddd import (
    Entity,
    EntityID,
    EntityImports,
    Import,
    ImportFormat,
    ResponseImport,
)


@dataclass
class Country(Entity):
    code: str
    name: str = ""

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"                       # a country is named by its code: BO, PE


@dataclass
class Partner(Entity):
    name: str
    vat: str = ""
    country_id: "EntityID[Country] | None" = None
    parent_id: "EntityID[Partner] | None" = None


class CommandImportPartners(Import[Partner, ResponseImport]):
    pass


@sales.feature(CommandImportPartners)
class PartnerImports(EntityImports[Partner]):
    pass


sales.add_dependency("repository", repository)

done = sales(
    CommandImportPartners(content=workbook, format=ImportFormat.EXCEL, origin="odoo"),
    ResponseImport,
)
done.created, done.updated, done.unchanged, done.skipped
```

As with `Get` and `Edit`, the base of the DTO names the entity and the response, so the Feature
reads both off the DTO, and the DTO has whatever name the project gives it. `EntityImports` is an
`EntityReads`: one Feature can serve an entity's screens and its imports.

`content` is the file's bytes. Over JSON (HTTP, JSON-RPC, MCP) it travels in base64, so an Excel
workbook reaches the bus through any wire without a route of its own.

## The file

Each record is one row, each field one column, and the headers are the names of the entity's
fields:

| Header | What it means | Cell |
|---|---|---|
| `name` | the field `name` | `ACME SRL` |
| `country_id` | the country named by its `DEFAULT_GET_ID`: the natural key it declares, or its identity | `BO` |
| `country_id/@key` | the country whose external key is this, in the import's origin | `base.bo` |
| `@key` | this record's own external key: the next import finds the record by it; the first creates the record and stores the key | `res_partner_42` |
| `id` | this record's identity, a UUID v7 | `01a0fb9a7e01…` |
| `lines/product_id`, `lines/quantity` | a child of the record; a row whose own cells are blank continues the record above it | `SKU-7`, `2` |
| `labels`, `labels/@key` | a many-to-many: the records it names, several in one cell separated by commas | `vip,retail` |

`/` walks from a field into the record it names; `@key` is that record's external key, which lives
beside the record ([below](#naming-a-record-from-outside)).

```csv
@key,name,country_id,parent_id/@key
p-1,ACME SRL,BO,
p-2,ACME Santa Cruz,BO,p-1
```

```json
[
  {"@key": "p-1", "name": "ACME SRL", "country_id": "BO"},
  {"@key": "p-2", "name": "ACME Santa Cruz", "country_id": {"code": "BO"}, "parent_id": {"@key": "p-1"}}
]
```

A JSON record names another one by any of its keys — `"BO"`, `{"code": "BO"}`, `{"@key": "base.bo"}`
or `{"id": "0192…"}` — and nests its children; a flat file holds one level of children, on the rows
below their record:

```csv
number,partner_id/@key,lines/product_id,lines/quantity
F-1,p-1,SKU-7,2
,,SKU-9,1
F-2,p-2,SKU-7,5
```

- **A reference names a record already stored, or one earlier in the file.** `p-2` names `p-1` as
  its parent because `p-1` comes first; the other way round, `p-2` is refused with
  `no Partner has @key 'p-1', stored or earlier in the file`. It is what lets the import read the
  file once.
- **A column that names no field is left out, not refused**, and the answer names it in
  `ignored` — a nested one by its path, `lines/colour`. `columns={"Razón social": "name",
  "Notas": None}` renames a header the file cannot change, or drops one on purpose.
- **A cell is made what its field reads.** A number in a text field is its text (Excel keeps a tax
  id as a number); a date Excel keeps is a date; a CSV's text is read by the field's type, dates as
  ISO 8601; with `decimal=","` a comma is the decimal separator and a point the thousands'.
- **The columns present are the fields written.** A column the file does not have leaves its field
  as stored; a blank cell clears it — an empty text, or `None`.
- **A CSV's delimiter is read off its header** — `,`, `;`, a tab or `|` —, and its encoding is
  `encoding` (`utf-8-sig` unless the import says `cp1252`).
- **A key repeated in the file updates the record above it**, as Odoo does, whether the two rows
  fall in one batch or in two. A dry run counts a key repeated in two batches as two records:
  nothing it read was stored for the second batch to find.
- **An `id` is a UUID v7**, read in any spelling (dashes, capitals); anything else is refused at
  its cell.

## Naming a record from outside

| By | When | Kept on |
|---|---|---|
| identity (`id`) | the file came out of this system: an export, edited and sent back | the record |
| natural key (`DEFAULT_GET_ID`) | the entity has a key of its business: an account code, a tax id, a SKU | the record |
| external key (`@key`) | the record has no key of its business, or it comes from another system that names it its own way — Odoo's `res_partner_42`, a row of the old spreadsheet | an `ExternalKey`, beside the record |

A record of the file is found by the first of its keys a stored record holds — its `@key`, then
its `id`, then its natural key —, and is new when none does. Two of its keys naming two different
stored records are refused: the file contradicts itself, and either guess would write a record over
another. A key is read as its field reads it — the cell `007` finds the code `7`.

**The identity is never derived from an outside key.** A UUID computed from the outside name
(`uuid5("odoo:res_partner_42")`) would need no table, and it is refused for three reasons: an
`EntityID` is a UUID v7 and nothing else; a v7 carries the moment the record was created —
ordering by id is ordering by time, `moment_of` reads it — and a derived one would lie about both;
and the identity would hang on a name another system owns: the day it corrects that name, every
foreign key would have to change. Odoo does the same: an external id is a row of `ir.model.data`
that points at the record, never the record's id.

### `ExternalKey`: one table per bounded context

```python
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import (
    external_key_table,
)

map_aggregates(registry, {ExternalKey: external_key_table("sales_external_keys", metadata)})
```

```
id, created_at, updated_at, version      an entity's columns
origin TEXT                               who named it: "odoo", "legacy_excel"
entity_type TEXT                          whose record: "Partner"
entity_id TEXT                            the record's identity
key TEXT                                  the name the origin gives it
UNIQUE (origin, entity_type, key)         one name, one record
INDEX (entity_type, entity_id)            the names of one record
```

- **An `ExternalKey` is an entity**, stored by the same repository as the records it names and in
  the same unit of work: a record and its key are stored together or not at all.
  `MemoryRepository` stores it the same way.
- **The origin is the import's** (`origin="odoo"`; `"import"` when it names none). Two origins
  naming one record are two keys of it.
- **One table per bounded context, as its events.** Two contexts that share a mapper registry each
  map a subclass (`class SalesExternalKey(ExternalKey)`) and name it on their Feature:
  `external_keys = SalesExternalKey`.
- **A file without `@key` never reads the table.** A record matched by its natural key or its id
  that brings an `@key` the store does not hold yet has it stored, so the next import finds it by
  that name.

## The process

Five steps, each a method of `EntityImports`, run a batch at a time (`batch_size`, 500). Override
one to change it; call `super()` to keep it and add.

| Step | Does | Leaves |
|---|---|---|
| `rows(dto)` | reads `content` as its `format` says | each row's cells, nested as JSON writes a record, with its place in the file: `(12,)` a CSV line, `("Partners", 12)` a sheet's row, `(3,)` a JSON item |
| `records(dto, rows)` | gathers each record's children, checks every name, makes each cell what its field reads, marks every record it names to be looked up | one `ImportRecord` per record: its values and the keys the file names it by |
| `resolve(dto, batch, keys)` | turns every key into an identity — each record's own, finding the record it updates, and each record it names | each record's identity and whether it is created or updated, the stored records it updates |
| `build(dto, batch)` | puts the values on the stored record or a new one (`apply_values`, as an `Edit` does), computes its fields and judges every `@check` (`recompute_whole`) | the aggregates to save; `UNCHANGED` when the file changes nothing of a stored one |
| `write(dto, batch)` | `repository.save` the batch, then the external keys the store did not hold | — |

- **A key is looked up once per import.** `resolve` asks the store only for the keys it has not
  seen, with one search per entity and kind of key per batch (`identities`, a method too): two
  hundred countries named by a million partners cost two hundred lookups, not a million. A
  record's own keys are asked batch by batch and not kept, so memory stays flat.
- **Correct before fast.** The write is a `save`: hooks run, `@check` is judged, the children are
  written with their root and the version is raised, as when a form is saved. What a save would
  refuse, `build` refuses first, so a refusal names the cell and nothing half-written waits.
- **A failure points at its cell**: the place in the file, then the field — a child by its
  position in the record, not by an identity the file never had.
- **A record that names a record left out is left out too**, and the keys of a record left out
  are forgotten: nothing written points at a record that never was.

## The transaction

An import is one unit of work: it commits whole or rolls back whole.

| `skip_invalid=` | When a record does not read, resolve or pass its checks |
|---|---|
| `False` (default) | nothing is written. From that record on the file is only read, so `ImportRefused` names every invalid record of the file, not only the first |
| `True` | the record is left out and named in the answer; the rest is written |

- **A refusal of the database** — a unique constraint a key did not foresee — rolls the whole
  import back, whatever `skip_invalid` says.
- **An `ApplicationService` that opened a unit of work already runs the import inside it**: what
  the import writes commits or rolls back with the rest.
- **A store that does not transact** (it does not claim `Transacts`) writes batch by batch, and
  the import says so in a warning: a failure leaves the batches before it written.
- **`mode`** refuses what the import must not do: `ImportMode.CREATE` refuses a record that
  exists, `ImportMode.UPDATE` one that does not; `CREATE_OR_UPDATE` is the default.

## The answer

```python
class ResponseImport(DataTransferObject):
    created: int
    updated: int
    unchanged: int
    skipped: list[FieldError]       # the records left out under skip_invalid=True
    ignored: list[str]              # the columns that name no field, left out
```

A failure is a `FieldError`, the shape a request that does not validate has on every wire: its
`pointer` is the cell in the file, its `detail` what is wrong, its `rule` the `@check` that
refused the value when one did.

```
FieldError(pointer="#/content/12/country_id", detail="no Country has code 'BX'")
FieldError(pointer="#/content/3/lines/1/quantity", detail="a line sells at least one unit",
           rule="InvoiceLine.sells_something")
FieldError(pointer="#/content/3/parent_id", detail="the Partner @key 'p-1' names is left out")
```

- **A refused file** is `ImportRefused` (`invalid`, 422) with every failure in the problem's
  `errors`, so the file is fixed in one round.
- **A dry run is Odoo's "Test" button.** `Import` is a `DryRunnable`: `dry_run=True` reads,
  resolves and judges the whole file, writes nothing, and answers a `Preview` —
  `Would create 1200, update 30 and leave 4 unchanged`, with the `ResponseImport` it would answer
  in `data` — or refuses as the import would.

## Several entities in one workbook

One import is one entity and its children. A workbook of several is an `ApplicationService` that
imports each sheet in the order the references go, in one unit of work; each sheet finds the keys
the sheets before it stored:

```python
@sales.app_service(CommandImportOpening)
class ImportOpening(ApplicationService):
    def execute(self, dto: CommandImportOpening) -> ResponseImportOpening:
        with self.repository.context():
            for command in (
                CommandImportCountries(content=dto.content, format=ImportFormat.EXCEL,
                                       sheet="Countries", origin="odoo"),
                CommandImportPartners(content=dto.content, format=ImportFormat.EXCEL,
                                      sheet="Partners", origin="odoo"),
            ):
                self.feature_bus(command, ResponseImport)
        return ResponseImportOpening()
```

## The way back: export, edit, import again

An `Export` written as `ExportFormat.CSV` or `ExportFormat.EXCEL` is the file this import reads —
what Odoo calls an import-compatible export. A person exports the partners, corrects three hundred
rows in a spreadsheet and imports the file: the same records are updated, none added, none lost.

```python
from sincpro_framework.ddd import Export, ExportFormat, Streamed


class QueryExportPartners(Export[Partner, Streamed]):
    pass


@sales.feature([CommandImportPartners, QueryExportPartners])
class PartnerImports(EntityImports[Partner]):     # an EntityReads: it answers Export too
    pass


workbook = b"".join(sales(QueryExportPartners(format=ExportFormat.EXCEL), Streamed))
# … edited in a spreadsheet …
sales(CommandImportPartners(content=workbook, format=ImportFormat.EXCEL), ResponseImport)
#   ->  ResponseImport(created=0, updated=300, unchanged=…)
```

What it writes, column by column:

| Column | Written as |
|---|---|
| `id` | the record's identity, first: importing it again updates that record |
| a field | its value as a cell shows it — in Excel a day is a date cell, a number a number |
| `country_id` | the natural key of the record it names (`BO`), each looked up once per export |
| `labels` | a many-to-many: the keys of its records, comma-separated |
| `lines/id`, `lines/…` | the children, the first beside its record, the rest in the rows below |
| a list, a value object | its JSON, which the import reads back into the field |

- **What a form cannot edit is left out**: the framework's fields (`version`, `created_at`), a
  field the database computes, one an `@compute` computes and nobody edits, a to-one record (its
  key field is written). The import would refuse the first and compute the others again.
- **The criteria picks the records and the columns**: its selection filters, its specification
  names the fields to write — the identity always travels.
- **Exact in both formats**: a number Excel cannot hold exactly (more than 15 digits) and a moment
  with its zone are written as text, a decimal never in exponent notation, so they come back as
  they went. The CSV is UTF-8 with its BOM, so Excel opens it with its accents.
- **One Excel sheet holds 1,048,576 rows**: a larger export is refused at that row, naming CSV.
- **No `@key` is written.** The identity already names each record; an external key is the name
  *another* system gives it.

## Stores

The import speaks the repository port alone — `fetch_all` with `in` to look keys up, `save` to
write, `context()` when the store transacts — and nothing of it is SQL. It runs on
`MemoryRepository` (`tests/ddd/importing/`) and on the SQLAlchemy `Repository`
(`tests/data_layer/orm/test_imports_sql.py`, on SQLite, and on Postgres when `SINCPRO_POSTGRES_URL` names one); a document store runs it once
its repository answers the port, `ExternalKey` one more collection there.

## Extending it

1. **One line**: the defaults above.
2. **Options of the same call**: `format`, `sheet`, `origin`, `mode`, `skip_invalid`, `columns`,
   `decimal`, `encoding`, `batch_size`, `dry_run`. They are fields of the DTO, so a project fixes
   its own by declaring them again:

   ```python
   class CommandImportPartners(Import[Partner, ResponseImport]):
       origin: str = "odoo"
       skip_invalid: bool = True
   ```

3. **A step overridden** on the Feature:

   ```python
   class PartnerImports(EntityImports[Partner]):
       def rows(self, dto: CommandImportPartners) -> Iterator[ImportRow]:
           ...                                  # a format of its own: the tax office's XML

       def build(self, dto: CommandImportPartners, batch: list[ImportRecord]) -> None:
           super().build(dto, batch)            # the file's values on each record, judged
           ...                                  # then a rule of this import alone
   ```

`ImportRecord`, `ImportRow`, `ImportOutcome` and the readers (`reading.rows_of`,
`excel.sheet_rows`) are in `sincpro_framework.ddd.importing`.

## What it costs

SQLite on disk, Python 3.12: 100,000 partners of four fields and a reference to 200 countries, in
batches of 500, one transaction.

| Work | Rows per second |
|---|---|
| reading CSV (`csv`) | 1,480,000 |
| reading JSON (`json`) | 2,350,000 |
| reading an Excel sheet (this reader, standard library) | 121,000 |
| reading an Excel sheet (`openpyxl`, read-only), for comparison | 28,000 |
| `save` of the same records in batches, nothing else | 21,200 |
| **the whole import of a CSV**, references by natural key | **12,400** |
| the whole import of an Excel sheet | 12,000 |
| the whole import of a CSV with an `@key` per record (its keys stored too) | 8,600 |

The write rows were measured in one run, back to back; on a laptop a run moves them ±15%, together.
What holds is the ratio: the whole import runs at about 60% of a bare `save`.

- **The write is most of it.** The import adds reading the cells, looking keys up and judging the
  rules to a `save` that would run anyway; a million partners import in about a minute and a
  half.
- **An external key per record costs a second write** — its `ExternalKey` row — and the lookup of
  each record's key; a file that names its records by a natural key does without both.
- **A write past the aggregate** — an insert through SQLAlchemy Core measured 149,000 rows per
  second, `COPY` on Postgres more — is the next step for files of tens of millions of rows; it
  skips hooks and rules, so it is chosen, never the default.

The way back, the same 100,000 partners, one run:

| Work | Rows per second |
|---|---|
| export as JSON Lines, for comparison | 65,000 |
| **export as CSV** | **62,000** |
| export as Excel | 46,000 |
| importing that CSV again, every record unchanged | 10,300 |
| importing that Excel sheet again, every record unchanged | 7,400 |

An export reads a page of 5,000 records at a time from one snapshot and writes it; the keys a page
names cost one search per entity, and only for the ones no page named before.
