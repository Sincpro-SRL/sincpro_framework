---
name: sincpro-framework-settings
description: Configure a sincpro_framework project — one YAML document plus the environment resolved into typed SincproConfig shapes, per bounded context or globally, with the cascade, $ENV secrets, env-by-path, provenance and the framework's own settings. Use whenever a task adds configuration, a settings shape, a secret, an environment variable, or wires settings into a bounded context.
---

# sincpro-framework-settings

A project's configuration is **one document** (its YAML file plus the environment). A **shape** is
any `SincproConfig` class. `build_config_obj(Shape, file, path)` resolves a shape against the
document by one rule. Depth: `docs/core/settings.md`, PRD_10.

## The three use cases

```python
from sincpro_framework.sincpro_conf import SincproConfig, Secret, build_config_obj


class SharedSettings(SincproConfig):              # what every context shares
    environment: Environment = Environment.TEST


class QRSettings(SharedSettings):                 # a context: shared + its own
    linkser_endpoint: str = "https://api.linkser.com"
    timeout: float = 10.0


class PaymentsSettings(SharedSettings):           # the global: shared + every context
    qr: QRSettings
    cybersource: CybersourceSettings


settings = build_config_obj(PaymentsSettings, "conf/payments.yml", "sincpro_payments_sdk")
```

```python
settings.cybersource.merchant_id                 # 1. the global
qr_bus.add_dependency("settings", settings.qr)   # 2. a context gets its own shape (self.settings.timeout)
qr = build_config_obj(QRSettings, "conf/payments.yml", "sincpro_payments_sdk.qr")   # 3. any shape, any path
```

`settings.qr` is an attribute of the singleton — the same object everywhere in the process.

## How a shape resolves

1. **The section.** `path` is dotted; its first key is the project's section (the **anchor**). A
   missing section is a `ValueError` naming the path.
2. **Nested shapes.** A field typed as a `SincproConfig` resolves at the section of its name.
3. **The cascade.** A shared field (inherited from a shape) takes the nearest section above that
   sets it, never above the anchor. A field a shape declares itself is never taken from above.
4. **Validation, once.** One pydantic call; everything missing or wrong is **one** `ValidationError`
   with full paths.

A shape read at a section without a dot (every project's `config.py`) gets its section exactly as
written — no cascade.

## The environment

- **`$ENV:NAME`** in the file reads the variable. Unset or unusable ⇒ the field's default, with an
  info log (settings build at import; a typo never takes the process down).
- **By path**, behind `env_prefix`: a root shape declaring `env_prefix = "PAYMENTS"` reads
  `PAYMENTS__QR__TIMEOUT` (→ `qr.timeout`) and `PAYMENTS__ENVIRONMENT`. A variable by path wins over
  the file. Without `env_prefix`, nothing is read by path — two SDKs in one process never read each
  other's variables.

## Secrets

```python
api_secret: Secret[str]                              # pydantic's Secret, re-exported
settings.cybersource.api_secret.get_secret_value()   # read on purpose; repr/str/logs show **********
```

A secret written literally in the file works but logs a warning naming its path — secrets belong in
the environment, through `$ENV:`.

## Provenance

```python
from sincpro_framework.sincpro_conf import describe_settings

for one in describe_settings(settings):
    print(one.path, one.type, one.value, one.source)
# qr.timeout   float   5.0   env PAYMENTS__QR__TIMEOUT
```

A source is `default`, `file <path> at <section>`, `inherited from <section>`, `env <NAME>`,
`default (<NAME> not set)` or `default (<NAME> cannot be used)`. A secret's value is never shown.

## The project layout

One `settings/` package outside the contexts — a module per shape, the singleton built in its
`__init__`. Contexts import it; it imports no context (no cycle). Scope is guidance, not a wall; a
team that wants the rule tests it with `settings_scope_violations`.

## The framework's own settings

The framework reads its log level/backend, OTLP endpoint and sampling, Sentry DSN, release, service
name and tenant from `sincpro_framework.sincpro_conf.settings`. A project's shared shape may inherit
`FrameworkSettings`; building it hands over every field the project set (a default or an unset
`$ENV:` is not handed over). The framework reads these when a bus sets itself up, so **build the
project's singleton before its buses**.

## Test override

```python
from sincpro_framework.testing import override_dependencies
with override_dependencies(qr_bus, settings=settings.qr.model_copy(update={"timeout": 1})):
    ...
```

## Related

- The bus and dependency injection: `sincpro-framework`
- Observability reads the same identity (`SERVICE`, `TENANT`): `sincpro-framework-observability`
