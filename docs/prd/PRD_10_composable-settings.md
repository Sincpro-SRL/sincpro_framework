# PRD_10: Settings — one document, any shape, one resolution

- **Status**: implemented, phases 1–3 (`sincpro_framework/settings/`, guide in
  [docs/core/settings.md](../core/settings.md)); phase 4 is the projects' own adoption. Where it
  differs from the text below:
  - **The cascade takes only shared fields** — a field a shape has from a shape it inherits
    (`QRSettings.environment` from `SharedSettings`), never one it declares itself. Without that
    rule a nested class written before PRD_10 (`postgresql: PostgresConf = PostgresConf()`, as the
    README has always shown) would take a same-named field from its parent; with it, every such
    class builds exactly as before. A context that re-declares a shared field owns it.
  - **A nested shape with a default and no section keeps its default**, as today; one without a
    default is built from the cascade.
  - **The environment by path is `$ENV:` sentinels placed at the path**, so a variable by path has
    the same fallback-to-default and log line as one written in the file.
  - **`FrameworkSettings` copies into the framework's `settings` object** the fields the project
    set (source other than a default) and reconfigures the log when a log field is among them —
    every framework module and SDK holds that object from import time, so it is written, not swapped.
  - **`describe_settings`** reports `default (<NAME> not set)` / `default (<NAME> cannot be used)`
    for a `$ENV:` that fell back; a value assigned after building keeps its build-time source.
  - **The scope check is `settings_scope_violations(package, GlobalShape)`**: a module's context
    is the first path part naming a section of the shape; a reading counts through an imported name.
  - **Equality ignores provenance**: a built object equals one written by hand with the same values.
- **Depends on**: `sincpro_framework.sincpro_conf` (`SincproConfig`, `$ENV:` sentinels, `build_config_obj`).
- **Compatible by construction**: `sincpro_siat_soap`, `sincpro_odoo_mcp`, `sincpro_ocr_service`,
  `sincpro_synthesis` keep their `config.py` as it is; `sincpro_payments_sdk` gains what it lacks.

## The use cases

1. **The global object.** Anyone may import the project's settings and read everything —
   `settings.environment`, `settings.billing.timeout`, `settings.transaction.limit`.
2. **A context's own object.** A bounded context may read a settings object that holds **the shared
   settings plus its own, and nothing of the other contexts** — `billing` sees `environment` and
   `billing.timeout`, never `transaction.limit`.
3. **Any shape.** A settings class may inherit everything, or declare a subset — the shape is the
   author's, pydantic's own; the framework resolves any shape from the same configuration, the same
   way.

Both 1 and 2 are always available; neither is closed.

## The model: one document, shapes over it

**The document** is the configuration as written: the project's YAML file, its `$ENV:` sentinels
resolved, overridden by the environment by path. There is one document per project.

**A shape** is any `SincproConfig` class. It declares which fields it wants; resolving it against the
document fills them. Nothing about the document says which shapes exist, and nothing about a shape
says which other shapes exist — so a context's shape cannot see another context: it does not
declare it.

```python
# sincpro_payments_sdk/settings/shared.py
class SharedSettings(SincproConfig):              # what every context shares
    environment: Environment = Environment.TEST
    log_level: Literal["INFO", "DEBUG"] = "INFO"


# sincpro_payments_sdk/settings/qr.py
class QRSettings(SharedSettings):                 # use case 2: shared + qr's own
    linkser_endpoint: str = "https://api.linkser.com"
    timeout: float = 10.0


# sincpro_payments_sdk/settings/cybersource.py
class CybersourceSettings(SharedSettings):
    merchant_id: str = ""
    api_secret: Secret[str]
    timeout: float = 30.0                         # the same name as qr's: each in its own section


# sincpro_payments_sdk/settings/payments.py
class PaymentsSettings(SharedSettings):           # use case 1: shared + every context
    qr: QRSettings
    cybersource: CybersourceSettings
```

```yaml
# conf/payments.yml — the one document
sincpro_payments_sdk:
  environment: TEST
  log_level: INFO
  qr:
    linkser_endpoint: $ENV:LINKSER_ENDPOINT
  cybersource:
    merchant_id: $ENV:CYBERSOURCE_MERCHANT_ID
    api_secret: $ENV:CYBERSOURCE_API_SECRET
    environment: PROD                              # a section may override what it inherits
```

**Why a class and not a list** like `compose([SharedSettings, QRSettings, CybersourceSettings])`: a
list would build the global class at runtime — no type the IDE can complete, no place to read what the
global holds. A class is the shape written once, typed, and the list is exactly its fields.

## One resolution

`build_config_obj(Shape, file, path)` — the function every project already calls — resolves any shape
at a path of the document:

1. **The section**: the document at `path` (`"sincpro_payments_sdk"`, or `"sincpro_payments_sdk.qr"`).
2. **Each field of the shape, in order**:
   1. a field whose type is itself a shape (`qr: QRSettings`) is resolved the same way, at the section
      of its name (`…qr`) — recursion;
   2. a field the section sets takes the section's value;
   3. a field the section does not set takes the value of **the nearest section above** that sets it —
      the cascade: `qr` does not set `environment`, so it takes `sincpro_payments_sdk.environment`;
   4. otherwise the field's default.
3. **The environment by path** wins over the file — only for a root shape that declares its prefix
   (`env_prefix: ClassVar[str] = "PAYMENTS"`): `PAYMENTS__QR__TIMEOUT=5` sets `…qr.timeout`. A shape
   that declares none reads the environment exactly as today (its `$ENV:` sentinels), so a stray
   `SOMETHING__X` in a process never changes a setting.
4. **Final**: the shape validated whole — one error listing every problem by path
   (`cybersource.api_secret: required (set CYBERSOURCE_API_SECRET)`).

The same document, two shapes, one rule:

```python
settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
settings.environment                  # TEST — the root sets it
settings.qr.environment               # TEST — qr does not, so it inherits the root's (the cascade)
settings.cybersource.environment      # PROD — its section overrides it
settings.cybersource.merchant_id      # use case 1: another context, read from the global

qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")
qr.linkser_endpoint                   # its own
qr.environment                        # TEST — inherited from the root, the same cascade
qr.merchant_id                        # AttributeError: QRSettings does not declare it (use case 2)
```

| Resolving | At path | Holds |
|---|---|---|
| `PaymentsSettings` | `sincpro_payments_sdk` | shared + every context — use case 1 |
| `QRSettings` | `sincpro_payments_sdk.qr` | shared (inherited) + qr — use case 2 |
| any shape the author writes | any path | what it declares, by the same rule — use case 3 |

### Any shape (use case 3)

```python
class Everything(PaymentsSettings):               # inherits everything, adds its own
    report_bucket: str = ""

class Collections(SharedSettings):                # a subset: two of the contexts, for one job
    qr: QRSettings
    bank_account: BankAccountSettings
```

Both resolve from the same document with the same call — `build_config_obj(Collections, file,
"sincpro_payments_sdk")` — holding what they declare and nothing else.

## How a project uses it

### Where the classes live: one `settings/` package, outside the contexts

```text
sincpro_payments_sdk/
├── settings/                  the project's settings package
│   ├── __init__.py            settings = build_config_obj(PaymentsSettings, …)   ← the singleton
│   ├── shared.py              SharedSettings
│   ├── qr.py                  QRSettings
│   ├── cybersource.py         CybersourceSettings
│   └── payments.py            PaymentsSettings
├── conf/payments.yml          the one document
└── apps/qr/ · apps/cybersource/ · apps/bank_account/
```

**Why outside the contexts**: the global shape imports every context's shape. If `QRSettings` lived in
`apps/qr/settings.py`, importing it would run `apps/qr/__init__.py` first — which builds the `qr` bus,
which imports the settings — a circular import at the first line. A `settings/` package that imports
no context has no cycle, and every context imports it freely. Each context still has its own file — in
`settings/`.

### The two flavours, both open

```python
from sincpro_payments_sdk.settings import settings

settings.qr.timeout                              # use case 1: the global, anything in it
settings.cybersource.merchant_id

qr_bus.add_dependency("settings", settings.qr)   # use case 2: a context's handlers get its own shape
# in a Feature of qr: self.settings.timeout — typed QRSettings, the same object as settings.qr
```

`settings.qr` is an attribute of the singleton: the same object everywhere in the process, never
resolved twice. A context on its own — a test, a service that hosts only it — resolves its shape with
the same call at its path; the values are the same, the object is its own.

Scoping is **guidance**, never a wall: a context may import the global. The framework only offers an
optional check (in `sincpro_framework.runtime.testing`, like `layer_violations`) that reports a context
reading another context's section — for a team that wants the rule.

### Read-only — recommended, opt-in

A project's shapes should be frozen (`model_config = ConfigDict(frozen=True)` on its `SharedSettings`):
the singleton is shared by every thread and every context, and a value changed at runtime is a bug
found late. A test hands a copy to the bus —
`override_dependencies(qr_bus, settings=settings.qr.model_copy(update={"timeout": 1}))`.

It is not imposed: `SincproConfig` and the framework's own settings stay mutable, because projects
assign to them today (`framework_settings.sincpro_framework_log_level = …` in `sincpro_siat_soap`,
and tests that set and restore values).

## In the framework: a package, the same entry point

`sincpro_conf.py` becomes the package `sincpro_framework/settings/`, in the framework's usual shape:

```text
sincpro_framework/settings/
├── domain/        SincproConfig, Secret, the resolution (sections, cascade, precedence)
├── adapters/      the sources: the YAML file, the environment by path
└── describe.py    describe_settings(settings): each value, masked if secret, and the layer it came from
```

`sincpro_framework.sincpro_conf` stays the public entry point (`SincproConfig`, `build_config_obj`,
`settings`) — every project imports it, and moving it would only break them.

**Not a separate repository**: it is small, the framework reads its own settings through it, and a
second package would only add a version to keep in step. **Not `pydantic-settings`**: settings are
core, and the core carries nothing beyond pydantic and PyYAML; its design (nested models, `__` paths)
is the reference.

**`FrameworkSettings`** (log level and backend, OTLP endpoint, Sentry DSN, release): a project's
`SharedSettings` may inherit it, and the framework reads them from the project's singleton — the
hand-copied `framework_settings.x = settings.x` lines in every `config.py` go away. Opt-in.

## No breaking change — the guarantee, and how it holds

Every behaviour a project relies on today stays, checked against the four projects that use it:

| Today | Stays because |
|---|---|
| `from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj, settings` | `sincpro_conf` stays the public entry point of the new package |
| `build_config_obj(Class, path, sub_key)` | same signature; a `sub_key` without a dot is read as today — none of the four has one (`sincpro_soap_sdk`, `sincpro_odoo_mcp`, `sincpro_ocr_service`, `sincpro_synthesis`) |
| a flat class, with no nested shapes | the cascade applies only to nested shapes — none of the four has one, so each builds exactly as today |
| `$ENV:` sentinels, and an unusable env value falling back to the default with a log line | unchanged |
| assigning to a settings object (`framework_settings.x = …`) | nothing is frozen unless a project's own shape asks for it |
| the environment | read by path only for a root shape declaring `env_prefix`; otherwise only the `$ENV:` sentinels, as today |

Everything new — nested shapes, the cascade, a dotted `sub_key`, `env_prefix`, `Secret`, frozen
shapes, `describe_settings`, `FrameworkSettings` — is reached only by a project that writes it. The
suites of `sincpro_siat_soap`, `sincpro_odoo_mcp`, `sincpro_ocr_service` and `sincpro_synthesis` run
against the implementation, unchanged, before it ships.

## Compatibility

| Project | Change needed |
|---|---|
| siat_soap, odoo_mcp, ocr_service, synthesis | none — a shape with no nested shapes resolves as today |
| payments_sdk | opt-in: a `settings/` package, a shape per context, replacing its adapters' `os.getenv` |
| every project | opt-in: inherit `FrameworkSettings` to drop the copied framework lines |

## Phases

1. `sincpro_framework/settings/` with the resolution: nested shapes, the cascade, `path` in
   `build_config_obj`, the environment by path behind `env_prefix`, `Secret`, frozen shapes as an
   option, the one-error validation for what is required and missing.
   Tests: the three use cases on one document; the cascade and a section overriding it; a context's
   shape blind to the others; any shape (a subset, one inheriting everything); an unsectioned class
   exactly as today (including the env fallback and assignment); the cycle-free `settings/` layout
   written to disk and imported; and the four projects' suites green against it.
2. `describe_settings`; the optional scope check in `testing`.
3. `FrameworkSettings`, read by the framework from the project's singleton.
4. payments_sdk (a shape per context) and synthesis (a shape per context) on it.

## Decisions to take

1. The environment path prefix — declared by the project's root shape (`env_prefix`, proposed), so it is
   both opt-in and collision-free between SDKs in one process (siat_soap and payments inside Odoo).
