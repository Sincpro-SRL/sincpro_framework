# Settings: one document, any shape, one resolution

A project's configuration is **one document** — its YAML file, plus the environment. A **shape** is
any `SincproConfig` class. `build_config_obj(Shape, file, path)` resolves a shape against the
document by one rule, whatever the shape is: the whole project, one bounded context, a subset.

```python
from sincpro_framework.sincpro_conf import SincproConfig, Secret, build_config_obj


class SharedSettings(SincproConfig):              # what every context shares
    environment: Environment = Environment.TEST


class QRSettings(SharedSettings):                 # a context: the shared settings + its own
    linkser_endpoint: str = "https://api.linkser.com"
    timeout: float = 10.0


class CybersourceSettings(SharedSettings):
    merchant_id: str = ""
    api_secret: Secret[str]


class PaymentsSettings(SharedSettings):           # the global: the shared settings + every context
    qr: QRSettings
    cybersource: CybersourceSettings


settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
```

```yaml
sincpro_payments_sdk:
  environment: TEST
  qr:
    linkser_endpoint: $ENV:LINKSER_ENDPOINT
  cybersource:
    merchant_id: M-1
    api_secret: $ENV:CYBERSOURCE_API_SECRET
    environment: PROD                 # this section overrides what it inherits
```

## The three use cases

```python
settings.cybersource.merchant_id                 # 1. the global: anything in it

qr_bus.add_dependency("settings", settings.qr)   # 2. a context: its handlers get its own shape
# in a Feature of qr: self.settings.timeout, self.settings.environment — typed QRSettings

qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")
# 3. any shape at any path — a test, a process that hosts only one context
```

`settings.qr` is an attribute of the singleton: the same object everywhere in the process. A
shape resolved alone at `sincpro_payments_sdk.qr` is equal by value to `settings.qr` — same
values, its own object.

## How a shape is resolved

1. **The section.** `path` is dotted: `"sincpro_payments_sdk.qr"` is the `qr` section inside the
   project's section. Its first key is the project's own section of the file — the **anchor**.
   A missing section is a `ValueError` naming the path.
2. **Nested shapes.** A field whose type is a `SincproConfig` class (or one that may be `None`) is
   resolved the same way, at the section of its name. One with a default whose section is absent
   keeps its default.
3. **The cascade.** A field the section does not set takes the value of the nearest section above
   that sets it, never above the anchor — **only for shared fields**: a field the shape has from a
   shape it inherits (`QRSettings` has `environment` from `SharedSettings`). A field a shape declares
   itself is never taken from above, and a context that re-declares a shared field owns it.
4. **Validation, once.** The whole tree is validated in one pydantic call: everything required and
   missing, or wrong, is **one** `ValidationError` whose locations are full paths
   (`("cybersource", "api_secret")`).

A shape read at a section without a dot — every project's `config.py` — has nothing above its
section, so it gets its section exactly as written: no cascade, nothing from beside it in the file.

## The environment

**`$ENV:NAME`** in the file reads the variable. When it is unset, or holds what the field cannot
accept, the field falls back to its default with an info log line — `settings` is built at import
time, and a typo in one deployment variable never takes the process down. A field whose default is
`None` takes `None` silently.

**By path, behind `env_prefix`.** A root shape that declares a prefix reads variables by path:

```python
class SharedSettings(SincproConfig):
    env_prefix: ClassVar[str] = "PAYMENTS"
```

```bash
PAYMENTS__QR__TIMEOUT=5          # qr.timeout
PAYMENTS__ENVIRONMENT=PROD       # environment — and every section that inherits it
```

A variable by path wins over the file and behaves as a `$ENV:` sentinel written at that path:
same fallback, same log line. Without `env_prefix` nothing is read by path — a stray `QR__TIMEOUT`
in the process changes nothing — so two SDKs in one process (siat_soap and payments inside Odoo)
never read each other's variables.

## Secrets

```python
api_secret: Secret[str]                           # pydantic's Secret, re-exported

settings.cybersource.api_secret                   # Secret('**********') — in repr, str, logs
settings.cybersource.api_secret.get_secret_value()   # read on purpose
```

A secret written literally in the file works, and logs a warning naming its path: secrets belong
in the environment, through `$ENV:`.

## Mutable unless a shape asks

`SincproConfig` objects, the framework's own `settings` included, stay assignable. A project that
wants its shapes read-only freezes them: `model_config = ConfigDict(frozen=True)` on its
`SharedSettings`. A test then hands the bus a copy —
`override_dependencies(qr_bus, settings=settings.qr.model_copy(update={"timeout": 1}))`.

## Where each value came from: `describe_settings`

```python
from sincpro_framework.sincpro_conf import describe_settings

for one in describe_settings(settings):
    print(one.path, one.type, one.value, one.source)
# environment              Environment    TEST        file conf/payments.yml at sincpro_payments_sdk
# qr.environment           Environment    TEST        inherited from sincpro_payments_sdk
# qr.linkser_endpoint      str            https://…   env LINKSER_ENDPOINT
# qr.timeout               float          5.0         env PAYMENTS__QR__TIMEOUT
# cybersource.api_secret   Secret[str]    **********  env CYBERSOURCE_API_SECRET
# cybersource.timeout      float          30.0        default
```

A source is `default`, `file <path> at <section>`, `inherited from <section>`, `env <NAME>`,
`default (<NAME> not set)` or `default (<NAME> cannot be used)`. A section handed to a context
(`describe_settings(settings.qr)`) describes itself by its own paths. A secret's value is never
shown. An object built by hand has no provenance: its sources read `assigned`.

## The project layout

Each bounded context owns its settings: a `settings.py` at the context's root, next to its
`__init__.py`, holding its shape and its object. What every context shares lives the same way in
`common/`. The YAML stays **one document** at the service root — the deployment's configuration,
where the cascade comes from.

```text
sincpro_payments_sdk/
├── conf/sincpro_payments_sdk.yml
└── apps/
    ├── common/
    │   └── settings.py        SharedSettings
    ├── qr/
    │   ├── settings.py        QRSettings(SharedSettings)
    │   │                      settings = build_config_obj(QRSettings, FILE, "sincpro_payments_sdk.qr")
    │   ├── __init__.py        qr = config_qr_framework(...); then import services
    │   └── infrastructure/
    │       └── dependencies.py    qr.add_dependency("settings", settings)
    └── cybersource/
        └── settings.py        CybersourceSettings(SharedSettings) → "sincpro_payments_sdk.cybersource"
```

- A context's shape resolves at its own path and still inherits from the sections above it:
  `qr.environment` comes from `sincpro_payments_sdk.environment` unless `qr:` sets it.
- `settings.py` imports only `common/` and the framework, so it never closes an import cycle, and
  `infrastructure/dependencies.py` imports it before the bus is created.
- The framework's own fields (`FrameworkSettings`: log, OTLP, Sentry, release) are the
  **process's**, not a context's. Set them in the root section only: each context's build hands
  them to the framework, so a context section that changed one would win or lose by import order.
- A view of the whole service (one `describe_settings` for every context) is a shape of its own,
  `ServiceSettings(SharedSettings)` with one field per context, built where it is needed — never
  imported by a context.

## Each context its own section — a check, for a team that wants it

Scoping is guidance, never a wall: a context may read the global. A team that wants the rule
tests it, like `layer_violations`:

```python
from sincpro_framework.runtime.testing import settings_scope_violations


def test_each_context_reads_only_its_own_settings():
    assert settings_scope_violations("sincpro_payments_sdk", PaymentsSettings) == []
```

It reads the source without importing it and returns data — a list of `SettingsScopeViolation`
(module, line, context, section) — never an exception. A module belongs to the context whose
section name first appears in its path (`apps/qr/...` is `qr`); a module outside every section —
a root entrypoint, `common/` — is not judged.

## The framework's own settings: `FrameworkSettings`

The framework reads its log level and backend, OTLP endpoint and sampling, Sentry DSN, release,
service name and tenant from `sincpro_framework.sincpro_conf.settings` (the `Variables` of the
root README). A project's shared shape may inherit `FrameworkSettings`:

```python
class SharedSettings(FrameworkSettings):
    environment: Environment = Environment.TEST
```

```yaml
sincpro_payments_sdk:
  app_release: $ENV:APP_RELEASE
  sentry_dsn: $ENV:SENTRY_DSN
  sincpro_framework_log_level: INFO
```

Building it hands the framework every one of those fields the project set — from the file, the
environment, or inherited — and configures the log again when a log field was among them. A field
left at its default, or a `$ENV:` variable nobody set, is not handed over: the framework's own
environment still decides it. The framework reads these when a bus sets itself up, so a
context's settings are built before its bus — which importing them from `dependencies.py`
guarantees. A project that
does not inherit `FrameworkSettings` changes nothing of the framework's settings.

## In the framework

```text
sincpro_framework/settings/
├── domain/        SincproConfig, Secret, FrameworkSettings; the resolution (sections, nested
│                  shapes, the cascade)
├── adapters/      the sources: the YAML file, the environment by path
├── building.py    build_config_obj
└── describe.py    describe_settings
```

`sincpro_framework.sincpro_conf` is the entry point every project imports: `SincproConfig`,
`build_config_obj`, `settings`, `DefaultFrameworkConfig`, `load_yaml_file`, `usable_env_value`,
and `FrameworkSettings`, `Secret`, `describe_settings`, `SettingDescription`. Why the design is
this one: [PRD 10](../prd/PRD_10_composable-settings.md).
