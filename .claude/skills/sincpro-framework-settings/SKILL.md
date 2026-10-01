---
name: sincpro-framework-settings
description: Configure a sincpro_framework project — one YAML document plus the environment resolved into typed SincproConfig shapes, per bounded context or globally, with the cascade, $ENV secrets, env-by-path, provenance and the framework's own settings. Use whenever a task adds configuration, a settings shape, a secret, an environment variable, or wires settings into a bounded context.
---

# sincpro-framework-settings

A project's configuration is **one document** (its YAML file plus the environment). A **shape** is
any `SincproConfig` class. `build_config_obj(Shape, file, path)` resolves a shape against the
document by one rule.

## Context

A service needs typed, validated configuration that differs per deployment, with secrets kept out
of the repository, read once at import. The framework gives one function that turns the YAML file
plus the environment into a pydantic object: the whole project's, one bounded context's, or any
subset. The same mechanism configures the framework itself (log, OTLP, Sentry, release, tenant).

- It is **not** a dependency-injection container: a context receives its settings object through
  `add_dependency("settings", ...)` like any other dependency (`sincpro-framework`).
- It is **not** a secrets manager: `Secret[str]` only masks a value read from the environment.
- It is **not** for values that change at runtime or per request: those are data (a repository)
  or the call's context (`sincpro-framework-core`).
- Don't read `os.environ` in Features or adapters: declare a field and let the shape resolve it.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `SincproConfig` | Base of every shape: a pydantic model that resolves `$ENV:` values | DTO | `from sincpro_framework.sincpro_conf import SincproConfig` |
| shape | Any `SincproConfig` subclass: the project's, a context's, a subset | DTO | (your class) |
| `build_config_obj(Shape, file, path)` | Reads the file, resolves `Shape` at the dotted `path`, validates once | function | `from sincpro_framework.sincpro_conf import build_config_obj` |
| `$ENV:NAME` | A value in the YAML that reads environment variable `NAME` | setting | (YAML syntax) |
| `env_prefix` | `ClassVar[str]` on the **root** shape: reads `<PREFIX>__<PATH>` variables | setting | attribute of `SincproConfig` |
| `Secret[T]` | pydantic's `Secret`: masked in repr/str/logs, `.get_secret_value()` reads it | DTO | `from sincpro_framework.sincpro_conf import Secret` |
| `describe_settings(obj)` | Each value by path, with its type and where it came from | function | `from sincpro_framework.sincpro_conf import describe_settings` |
| `SettingDescription` | One row of `describe_settings`: `path`, `type`, `value`, `source` | DTO | `from sincpro_framework.sincpro_conf import SettingDescription` |
| `FrameworkSettings` | The framework's own fields, inherited by a project's shared shape | DTO | `from sincpro_framework.sincpro_conf import FrameworkSettings` |
| `DefaultFrameworkConfig` | The shape of the framework's own settings | DTO | `from sincpro_framework.sincpro_conf import DefaultFrameworkConfig` |
| `settings` (framework's) | The framework's settings object, built at import | setting | `from sincpro_framework.sincpro_conf import settings` |
| `SINCPRO_FRAMEWORK_CONFIG_FILE` | Env var: another file for the framework's own settings | setting | (environment) |
| `load_yaml_file` | Reads the YAML document as a dict | adapter | `from sincpro_framework.sincpro_conf import load_yaml_file` |
| `settings_scope_violations` | Test helper: a context reading another context's section | function | `from sincpro_framework.testing import settings_scope_violations` |
| `override_dependencies` | Test helper: swap a registered dependency (a settings copy) | function | `from sincpro_framework.testing import override_dependencies` |

Look-alikes: the **framework's** `settings` (`sincpro_framework.sincpro_conf.settings`) is not the
**project's** singleton (`<pkg>.settings.settings`). `FrameworkSettings` is the class a project
inherits; `DefaultFrameworkConfig` is the shape the framework builds for itself.

## Architecture

**In the framework** (`sincpro_framework/settings/`, primary dependencies only: pydantic, PyYAML):

```text
settings/domain/      SincproConfig, Secret, FrameworkSettings, DefaultFrameworkConfig;
                      resolution: sections, nested shapes, the cascade
settings/adapters/    the document's sources: the YAML file, the environment by path
settings/building.py  build_config_obj
settings/describe.py  describe_settings
sincpro_conf.py       the entry point projects import; builds the framework's own `settings`
                      from conf/sincpro_framework_conf.yml (or $SINCPRO_FRAMEWORK_CONFIG_FILE)
```

No ports and no optional extras: the sources are plain functions.

**In a consumer service:**

```text
<pkg>/
├── conf/<pkg>.yml              the one document; secrets as $ENV:NAME
├── settings/                   outside every context: imports no context, so no import cycle
│   ├── __init__.py             settings = build_config_obj(ProjectSettings, FILE, "<pkg>")
│   ├── shared.py               SharedSettings(FrameworkSettings or SincproConfig)
│   ├── <ctx>.py                <Ctx>Settings(SharedSettings)
│   └── project.py              ProjectSettings(SharedSettings): one field per context
└── domains/<ctx>/
    ├── __init__.py             bus = config_<ctx>_framework("<ctx>"); then import services
    ├── infrastructure/
    │   └── dependencies.py     bus.add_dependency("settings", settings.<ctx>)
    └── services/               self.settings.timeout
```

A project with one flat shape keeps a root `config.py` with
`build_config_obj(Config, FILE, "<pkg>")` instead of the package.

**One resolution:**

```text
YAML file ─┐
           ├─ document ─ section at "<pkg>.<ctx>" ─ nested shapes ─ cascade ─ $ENV: ─ pydantic (once)
env vars ──┘  (<PREFIX>__PATH placed as $ENV: at its path)                        └─ object + sources
```

## Mistakes an agent makes

- **A typo in a YAML key.** Unknown keys are ignored and the field keeps its default, silently.
  Check `describe_settings(...)`: the field's source reads `default`.
- **A secret or endpoint with a default and an unset `$ENV:`.** It falls back to the default with
  only an info log (`None` for a `None` default, without any log). Give a value that must come from
  the environment no default, so a missing variable is a `ValidationError` at import.
- **A plain `Enum` field.** Shapes store an enum's **value** (`use_enum_values`), so
  `settings.environment == Environment.PROD` is `False` for a plain `Enum`. Use `StrEnum`.
- **A context shape declaring a field it means to share.** Only an **inherited** field cascades
  from the sections above; one the shape declares itself takes its own section or its default.
  Put shared fields on `SharedSettings` and inherit.
- **`env_prefix` on a nested shape.** Only the shape passed to `build_config_obj` is read for it;
  elsewhere it does nothing, and `<PREFIX>__...` variables are ignored.
- **`$ENV:` inside a dict or list value.** Only a field's own string value is resolved; inside a
  mapping it stays the literal text `$ENV:NAME`. Make the entry a field (or a nested shape).
- **Calling `build_config_obj` in several modules.** Each call re-reads the file and returns a new
  object. Build the singleton once, in `settings/__init__.py`, and import it.
- **Creating a bus before building the project's settings.** The framework reads its own settings
  when a bus is created (the context map) and when it is built (log, OTLP, Sentry); a
  `FrameworkSettings` built after that is missed by that bus, with no error. Import the settings
  package first.
- **Mutating the singleton at runtime.** It is shared by every thread and context. Freeze it
  (`model_config = ConfigDict(frozen=True)` on `SharedSettings`) and override a copy in tests.

## The three use cases

```python
from enum import StrEnum

from sincpro_framework.sincpro_conf import SincproConfig, Secret, build_config_obj


class Environment(StrEnum):
    TEST = "TEST"
    PROD = "PROD"


class SharedSettings(SincproConfig):              # what every context shares
    environment: Environment = Environment.TEST


class QRSettings(SharedSettings):                 # a context: shared + its own
    linkser_endpoint: str = "https://api.linkser.com"
    timeout: float = 10.0


class CybersourceSettings(SharedSettings):
    merchant_id: str = ""
    api_secret: Secret[str]                       # no default: required


class PaymentsSettings(SharedSettings):           # the global: shared + every context
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

```python
settings.cybersource.merchant_id                 # 1. the global
qr_bus.add_dependency("settings", settings.qr)   # 2. a context gets its own shape (self.settings.timeout)
qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")   # 3. any shape, any path
```

`settings.qr` is an attribute of the singleton: the same object everywhere in the process. A shape
resolved alone at its path is equal by value, but its own object.

## How a shape resolves

1. **The section.** `path` is dotted; its first key is the project's section (the **anchor**). A
   missing section is a `ValueError` naming the path.
2. **Nested shapes.** A field typed as a `SincproConfig` resolves at the section of its name; one
   with a default and no section keeps its default.
3. **The cascade.** A shared field (inherited from a shape) takes the nearest section above that
   sets it, never above the anchor. A field a shape declares itself is never taken from above, and a
   context that re-declares a shared field owns it.
4. **Validation, once.** One pydantic call; everything missing or wrong is **one** `ValidationError`
   with full paths (`("cybersource", "api_secret")`).

A shape read at a section without a dot (a flat `config.py`) gets its section exactly as written.

## The environment

- **`$ENV:NAME`** in the file reads the variable. Unset or unusable ⇒ the field's default, with an
  info log (settings build at import; a typo never takes the process down). A required field has no
  default to fall back on and fails validation.
- **By path**, behind `env_prefix`: a root shape declaring `env_prefix: ClassVar[str] = "PAYMENTS"`
  reads `PAYMENTS__QR__TIMEOUT` (→ `qr.timeout`) and `PAYMENTS__ENVIRONMENT`, as if the file said
  `$ENV:` there. A variable by path wins over the file. Without `env_prefix`, nothing is read by
  path, so two SDKs in one process never read each other's variables.

## Secrets

```python
api_secret: Secret[str]                              # pydantic's Secret, re-exported
settings.cybersource.api_secret.get_secret_value()   # read on purpose; repr/str/logs show **********
```

A secret written literally in the file works but logs a warning naming its path: secrets belong in
the environment, through `$ENV:`.

## Provenance

```python
from sincpro_framework.sincpro_conf import describe_settings

for one in describe_settings(settings):
    print(one.path, one.type, one.value, one.source)
# qr.timeout   float   5.0   env PAYMENTS__QR__TIMEOUT
```

A source is `default`, `file <path> at <section>`, `inherited from <section>`, `env <NAME>`,
`default (<NAME> not set)`, `default (<NAME> cannot be used)`, or `assigned` (built by hand). A
section handed to a context describes itself by its own paths. A secret's value is never shown.

## The framework's own settings

A project's shared shape may inherit `FrameworkSettings`; building it hands the framework every
field the project set (a default or an unset `$ENV:` is not handed over, so the framework's own
environment still decides it) and reconfigures the log when a log field is among them. The fields
and their variables: [references/framework-settings.md](references/framework-settings.md).

## Scope check and test override

```python
from sincpro_framework.testing import override_dependencies, settings_scope_violations


def test_each_context_reads_only_its_own_settings():
    assert settings_scope_violations("sincpro_payments_sdk", PaymentsSettings) == []


with override_dependencies(qr_bus, settings=settings.qr.model_copy(update={"timeout": 1})):
    ...
```

Scope is guidance, not a wall: the check is for a team that wants the rule.

The framework repository holds the long-form guide (`docs/core/settings.md`) and the design
(PRD_10); this skill does not depend on them.

## Related

- The bus and dependency injection: `sincpro-framework`
- Observability reads the same identity (`APP_RELEASE`, `TENANT`): `sincpro-framework-observability`
- A context hosted by another service (`context_map`): `sincpro-framework-entrypoints`
