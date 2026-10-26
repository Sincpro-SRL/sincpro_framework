# PRD 32 — The context keeps the caches and the idempotency: a facade on every bus

- **Status**: done, 2026-10-24 — not committed. Checklist in §9, summary in §11.
- **Builds on**: PRD_13 (caching primitives and idempotency), PRD_22/24 (the context as a component,
  shared context), the leases and circuits kept where the context is kept. Breaking changes
  accepted for the best developer experience; idempotency keys stay readable across the upgrade.
- **Goal**: a cache and an idempotency that a newcomer uses with one line and nothing configured,
  that every bounded context of a service gets on its bus, that live where the context is kept —
  memory, or the Redis the project kept its context in — and that never close a door: every
  strategy of `Cache` and `Idempotency` stays one argument away, and a store of a project's own is
  still injected as a dependency.

## 1. What exists, measured (2026-10-24)

| Piece | Where it keeps its state today |
|---|---|
| shared context, drafts, pipeline runs | the `ContextStore` (its entries) |
| leases | the `KeyValueStore` the context stands on (`KeyValueContexts.store`, `{prefix}:lease`) |
| circuits | the same `KeyValueStore`, resolved per call from the running context |
| `Cache` | explicit; with none, objects in this process, never encoded (what a credential needs) |
| `QueryCaching` | explicit `KeyValueStore` |
| `Idempotency` | explicit and required |
| cron runs, `Subscriber` receipts, queue inbox | explicit |

What a project writes today to cache one function on the Redis its context already lives on: a
`Cache(RedisKeyValue(...), namespace=...)`, a `CachePolicy`, a `JsonCodec`, a key by hand — and the
same store passed again to `Idempotency` and `QueryCaching`. Nothing tells which bounded context a
key belongs to, and a value can be served to another tenant unless `vary_by` was remembered.

**A principle kept**: `ContextStore` says *"Not a cache — a cache keeps what can be computed
again; this keeps what a flow is."* So a cache is never an entry of the context: it lives on the
**`KeyValueStore` the context stands on**, in a section of its own, beside the leases — the context
is what knows which store that is.

## 2. What others do (research)

| | Cache | Idempotency |
|---|---|---|
| Django | `CACHES["default"]` configured once; `cache` imported anywhere; named caches for more | — |
| Spring | `@Cacheable` over a `CacheManager`; caches named per use | — |
| Python libraries (cashews, aiocache) | one decorator, the strategy as an argument or a named variant | — |
| Stripe | — | a key per request, answers replayed for 24 h, a key reused with other parameters refused, a request still running refused |
| IETF `Idempotency-Key` draft | — | the header; 400 without it where required, 409 while in progress, 422 for a key reused with another payload |
| Stripe-style keys on Postgres | — | the record and the write committed in one transaction: exactly once, not best effort |

What this framework already has of the right column: the header (FastAPI), 409 `AlreadyInProgress`,
422 `KeyReused` with a payload fingerprint, 400 `IdempotencyKeyMissing`, `idempotency_key()` on the
Command, and the `IdempotencyRecords` port whose docstring asks for a transactional implementation —
which the framework does not ship. `DatabaseNumbering` already shows how a SQL adapter joins the unit
of work in play (`unit_in_play.in_play`); a record kept the same way is exactly once.

## 3. The design

### 3.1 Two facades, one per concern, on every bus and global

```python
@billing.cache                                            # the default
def exchange_rate(currency: str) -> Decimal: ...

@billing.cache.ttl(timedelta(minutes=5))                   # a strategy, by its name
@billing.cache.sliding(idle_for=timedelta(minutes=30), at_most=timedelta(hours=8))
@billing.cache.versioned(current=catalog.version)
@billing.cache.ttl(timedelta(minutes=5), fail_safe=timedelta(hours=1))
@billing.cache(CachePolicy(...))                            # anything Cache can do

@billing.feature(CommandCharge)
@billing.idempotency                                       # the default: once per request, 24 h
class Charge(Feature): ...

@billing.idempotency(expires_after=timedelta(hours=1), wait_for_completion=timedelta(seconds=5))
@billing.idempotency.exactly_once(records)                 # the record in the write's transaction

from sincpro_framework import cache, idempotency           # the same, for a service with no buses of its own
```

| Facade | Default (`@facade`, `@facade()`) | By parameters | By strategy |
|---|---|---|---|
| `bus.cache`, `cache` | `TimeToLive(60 s, stale 10 s)`, waits at most 1 s for a value another caller computes | `fail_safe=`, `vary_by=` on every form | `.ttl(ttl, stale_for=, jitter=)`, `.sliding(idle_for, at_most)`, `.versioned(current, kept=, trusted_for=)`, `facade(CachePolicy)` |
| `bus.idempotency`, `idempotency` | once per request, replayed 24 h, a duplicate in flight refused at once (409) | `expires_after=`, `in_progress_for=`, `wait_for_completion=`, `vary_by=` | `.exactly_once(records)` |

A strategy method is added only for a strategy with a name of its own and a real case;
combinations go through `facade(CachePolicy(...))`.

**Every strategy object is a decorator and a tool by hand** — what an adapter, which never imports a
bus, uses:

```python
class SiatClient:                                          # adapters/: injected, never imported
    def __init__(self, soap: SoapClient, cache: CacheFacade) -> None:
        self.soap = soap
        self.products = cache.ttl(timedelta(hours=1))

    def products_of(self, nit: str) -> list[Product]:
        return self.products.get(("products", nit), lambda: self.soap.products(nit))

# infrastructure/dependencies.py — the one place that holds the bus
billing.add_dependency("siat", SiatClient(soap, cache=billing.cache))
```

**Defaults never block**: a store that fails is bypassed and the call answered from the process; a
caller waits at most a second for a value being computed; a duplicate request in flight is refused
at once rather than held.

### 3.2 Where it is kept: the context, always by default

```
bus.cache / bus.idempotency   → the bus's context store, when it has one (bus.store_context_in(...))
                              → else the process's (store_context_in(...))
                              → else this process's memory
cache / idempotency (global)  → the process's → else memory
```

One internal function answers «the `KeyValueStore` of the context» for every component — circuits
included — over a new `ContextStore.key_value_store()` (default: the store its leases stand on).
Configuring the process store reads in one line, in code — the context's own rule, no environment
variable turns sharing on:

```python
store_context_in(RedisKeyValue(redis.Redis.from_url(settings.redis_url)))   # the whole service
billing.store_context_in(KeyValueContexts(RedisKeyValue(billing_redis)))       # a bounded context of its own
```

| The three ways | Code | Configuration |
|---|---|---|
| one store for everything | `@cache` or `@billing.cache` | nothing (memory), or `store_context_in(redis)` |
| per bounded context, one Redis | `@billing.cache`, `@sales.cache` — separated by the bus's name | `store_context_in(redis)` |
| per bounded context, its own Redis | the same | `billing.store_context_in(...)` in its `framework.py` |

### 3.3 Keys

`namespace (the bus's name, or the global one) + the function or the Command + its arguments written
canonically (self of a method left out) + owner_of(context, (user_id, *vary_by))` — the rule drafts
and pipeline runs follow: `tenant_id`, `tenant_ids`, `user_id`, empty when the context says nothing,
so nothing is imposed on a service with no tenants and none is mixed in one that has them. An answer
is encoded by its function's return type on a shared store; a function that declares none is kept in
the process only.

**Idempotency across the upgrade**: a run first reads the key the previous release wrote (the
Command, its request key, the values of `vary_by`); a completed answer there is replayed, so a retry
that crosses the deploy never runs the write twice.

### 3.4 What stays, what goes

| Stays | Goes (the work in progress of 2026-10-24) |
|---|---|
| `Cache(store)`, `QueryCaching(store)`, `Idempotency(store)` built and injected — a store of a project's own | module-level `cached`, `once`, `DEFAULT`, `context_cache`, `use_context().cache(...)`, the `name` parameter, the `"default"` name |
| `Cache()` with no store: objects in the process, never encoded | — |
| every strategy class (`TimeToLive`, `Sliding`, `ExternalVersion`, `FailSafe`, `CachePolicy`) | — |

`QueryCaching` gains its place on the facade, bound to its bus's store:
`billing.cache.queries(QueryBalance, QueryCachePolicy(...))`.

## 4. Out of scope

Cron runs, `Subscriber` receipts and the queue inbox defaulting to the context's store; one prefix
scheme for every key in Redis. Each is a change of its own (§10).

## 9. Status

- [x] 1. Research, decisions, this PRD
- [x] 2. Core: store resolution (`ContextStore.key_value_store`, `store_context_in`), circuits on it, `CacheFacade` + strategies, `IdempotencyFacade`, the bus mixin, the global facades; the 2026-10-24 work in progress removed — `BusStoreMixin` (`bus.store()`, `bus.cache`, `bus.idempotency`), root `cache` / `idempotency` / `store_context_in`; an answer the previous release kept in no namespace is replayed; `tests/data_layer/caching/test_facades.py` (26) replaces the WIP test; caching + context + circuits + remote: 666 passed
- [x] 3. `bus.cache.queries(Query, QueryCachePolicy)` — one `QueryCaching` per bounded context, namespaced by its name and bound to its store (`store_of`), so a write invalidates on that store from any context; `bus.cache.queries()` answers it for `invalidate_on_commit`; the policy defaults to the cache's minute (1 s wait); the global cache refuses a Query; `QueryCaching()` with no store reads the running context through `current_store()`; caching suite: 262 passed
- [x] 4. `DatabaseIdempotencyRecords` + `idempotency_table`, `.exactly_once(records)` — the port gains `transaction()` (nothing by default); `Idempotency.run` runs the use case and `complete` inside it, so on the database they are one commit; the claim and the release commit on their own (a duplicate is refused at once, never held on the row); a claim or an answer past its time is taken over; a release that fails is said, never raised over the write's error; `tests/data_layer/orm/test_idempotency_records.py` (contract on SQLite racing 16 claims + exactly once: replay, failed write, failed completion rolls the write back, duplicate in flight, takeover) — Postgres not run locally (the only server up is Odoo's: the suite drops its schema); data_layer + entrypoints: 1995 passed
- [x] 5. Tests: facades, replicas over one Redis, per-BC store, global, adapters by injection, keys, idempotency upgrade, defaults that never block, the transactional records contract — `test_facades.py` 32 (two clients on one `FakeServer` per replica, bounded contexts apart on one Redis, a bus's own store, the order bus → process → memory, `store_context_in` with either store, a store of a project's own with no `KeyValueStore`, a store down, 409 at once, the previous release's answer replayed) + `test_idempotency_records.py` 9; `facades.py` put in dependency order; full suite: 4424 passed, the two failures left are the caching README (phase 6)
- [x] 6. Docs (caching README around the facades and the three ways, docs/README "By problem", core README) and skills — caching README: "The simple way: `bus.cache` and `bus.idempotency`" (runnable: every strategy, an adapter handed the facade, `@payments.idempotency`, `store_context_in`, a bus with a Redis of its own, the defaults table), `bus.cache.queries` in the QueryCaching section, "Exactly once: the answer in the write's transaction" (runnable on SQLite); docs/README "By problem" + Caching row; core README row; context-manager and circuits pages on the store rule; skills: caching (abstractions, consumer layout, flow, mistakes, both references), context (+ reference), core, persistence, router; tests/docs: 29 passed
- [x] 7. Final: black, isort, pyright 0, full suite green, no `*`/`/` markers, no leftover names; summary for the user — `make format-python` (autoflake, isort, black) touched nothing outside this work; pyright `sincpro_framework` 0 and `tests` 0; full suite 4427 passed, 34 skipped (no Postgres); no signature markers in the new code; no `cached`, `once`, `context_cache`, `use_context().cache`, `DEFAULT` left; summary in §11

## 10. Open

- A replay marker for the caller (Stripe's `Idempotent-Replayed`): the outcome is observed today,
  not told to the transport.
- Defaulting cron runs, receipts and the inbox to the context's store.

## 11. Resumen para el usuario (2026-10-24)

**Qué quedó.** Cada bounded context tiene ahora su cache y su idempotencia en el bus, sin construir
nada, guardados donde se guarda su contexto:

```python
@billing.cache                                         # 1 minuto, 10 s stale, espera ≤ 1 s
@billing.cache.ttl(timedelta(minutes=5), fail_safe=timedelta(hours=1))
@billing.cache.sliding(timedelta(minutes=30), timedelta(hours=8))
@billing.cache.versioned(catalog.version)
@billing.cache(CachePolicy(...))                        # cualquier combinación
billing.cache.queries(QueryBalance)                    # QueryCaching del contexto, en su store

@billing.idempotency                                   # una vez por request, 24 h, 409 al instante
@billing.idempotency(expires_after=..., vary_by=...)
@billing.idempotency.exactly_once(records)             # la respuesta en la misma transacción

from sincpro_framework import cache, idempotency, store_context_in   # los globales del servicio
store_context_in(RedisKeyValue(redis))                  # una línea: todo el servicio en Redis
```

- **Dónde se guarda** (una sola regla, la misma de leases y circuits): el store del bus
  (`billing.store_context_in(...)`) → el del proceso (`store_context_in(...)`) → memoria. Se lee en cada
  llamada, así que un store puesto después de declarar el decorador es el que se usa. Las keys
  llevan el nombre del bus: dos bounded contexts en el mismo Redis nunca se leen entre sí. Los tres
  modos: todo en un store (nada o `store_context_in`), por BC en el mismo Redis (igual, separados por
  nombre), por BC con su propio Redis (`billing.store_context_in`).
- **Keys**: función/Command + argumentos canónicos (sin `self`) + de quién es según el contexto
  (`tenant_id`, `tenant_ids`, `user_id`, vacíos si el contexto no los trae) + `vary_by`. Nada se
  impone a un servicio sin tenants.
- **Adapters**: nunca importan el bus; reciben la facade en `dependencies.py`
  (`SiatClient(cache=billing.cache)`) y usan `strategy.get(key, compute, codec)` / `.forget(key)`.
- **Defaults que no bloquean**: store caído → se salta y responde el proceso; ≤ 1 s esperando a otro
  caller; duplicado en vuelo → 409 inmediato.
- **Idempotencia nueva — exactamente una vez**: `DatabaseIdempotencyRecords` +
  `template_table.idempotency_table(...)` (en `data_layer.orm`). El claim se comitea solo (todas las
  réplicas lo ven), el use case corre dentro de una unidad de trabajo a la que se unen los
  repositorios de esa base, y la respuesta se comitea con la escritura: si el proceso muere antes,
  no queda ni la escritura ni la respuesta. El puerto `IdempotencyRecords` ganó `transaction()` (por
  defecto nada: best effort). Un `release` que falla se loguea y no tapa el error del write.
- **Compatibilidad**: una respuesta que un `Idempotency(store)` de la versión anterior completó se
  re-sirve con `bus.idempotency` sobre el mismo store — un retry que cruza el deploy no escribe dos
  veces.
- **Se fue** (era WIP de hoy, nunca publicado): `cached`, `once` y `DEFAULT` a nivel módulo,
  `context_cache`, `use_context().cache(...)`, el parámetro `name`, el literal `"default"`.
  **Se queda**: `Cache(store)`, `QueryCaching(store)`, `Idempotency(store)` construidos e inyectados
  a mano; `Cache()` sin store = objetos en proceso, nunca codificados (credenciales).

**Archivos nuevos**: `context/entrypoint/store.py` (resolución del store), `context/mixins/bus_store.py`
(`bus.store()`, `bus.cache`, `bus.idempotency`), `data_layer/caching/entrypoint/facades.py`,
`data_layer/orm/sqlalchemy/entrypoint/idempotency_records.py`, `template_table/idempotency.py`,
`tests/data_layer/caching/test_facades.py` (32), `tests/data_layer/orm/test_idempotency_records.py` (9).

**Verificado**: pyright 0/0 (paquete y tests), suite completa 4427 passed / 34 skipped, docs
ejecutables verdes, formateo del Makefile sin cambios fuera de este trabajo.

**Para tener en cuenta**:
- Postgres **no se corrió** localmente: el único servidor levantado es el de Odoo (`odoo-17-db-1`) y
  la suite hace `DROP SCHEMA public`. Correr `SINCPRO_POSTGRES_URL=... make test` contra un Postgres
  de pruebas antes de publicar, en especial `test_idempotency_records.py`.
- Lo que cambia respecto de la versión publicada: la key de `once()` ahora incluye de quién es la
  llamada (tenant/usuario del contexto) y el namespace del bus — cubierto por el replay de la key
  anterior. Ningún API publicado se quitó: `QueryCaching`, `Cache` e `Idempotency` aceptan lo mismo
  que antes (el store pasó a ser opcional y `QueryCaching` ganó `store_of` al final).
- `invalidate_on_commit(database, cache, *aggregates)` conserva su `*` (código previo, no tocado).
- Nada commiteado: el commit lo hacés vos.


## 12. Pendientes — después del refactor de capas (2026-10-24)

Decididos con el usuario; se hacen **después** de reordenar el framework en capas (kernel →
plataforma → runtime → componentes → herramientas, y para quién es cada API: pública, SPI de
extensión, interna del framework).

1. **Niveles del contexto y un contexto global con N réplicas que funcione bien.** Hoy `process` y
   `global` se guardan como un único JSON por clave: una réplica que escribe `plan` borra el
   `maintenance` que otra escribió, un default nuevo del código se ignora si la clave ya existe, y
   un objeto guardado con clave de texto desaparece tras el refresco (reproducido). Dirección: un
   HASH por nivel (una escritura = un campo), la versión en el mismo paso atómico, los defaults
   con "set if absent", los valores que no viajan aparte. `KeyValueContexts.save` (INCR + SET no
   atómicos) entra aquí.
2. **Aislamiento por ejecución.** Poder distinguir y leer a qué ejecución pertenece algo: una
   ejecución de ApplicationService, de Feature, el request de un entrypoint, un hook — qué ve cada
   nivel, qué escribe y qué se le pasa a otro proceso.

**Decisión registrada:** los drafts y la edición de formularios son de la entidad (`ddd/editing`),
no del contexto; hoy `Drafts` vive en `context/` y se moverá con el refactor.

### Código sospechoso reportado al documentar (no corregido)

Por severidad. Cada uno se revisa y se corrige o se descarta en su propio cambio.

**Puede fallar en producción**
- `entrypoints/adapters/rest/routing.py` `failure_answer`: el wire REST (Starlette) devuelve el
  mensaje crudo de errores no internos; FastAPI, JSON-RPC y gRPC lo filtran (`said_to_the_caller`).
- `settings/domain/config.py` `resolve_env_variables`: un campo obligatorio con `$ENV:` sin
  definir recibe `PydanticUndefined` como valor (error confuso; reproducido).
- `observability/tracing/span_context.py`: `with_trace(trace_id=...)` sin `span_id` nunca engancha
  el trace (uuid con guiones en `int(..., 16)`).
- `observability/api.py` `Observability.start`: sin `logger` no registra nada y pasa `None` a
  `setup_tracing`.
- `auth/adapters/service_token_provider.py` `_claims`: un header de token raro da 500 en vez de
  `Unauthenticated`.
- `event_driven/entrypoint/subscriber.py` `AsyncSubscriber.handle`: un fallo al reconstruir el
  evento corta los buses siguientes, y el store de receipts bloquea el event loop.
- `pipelines/pipeline.py` `execute`: si el Command `always` falla, tapa la excepción del paso.
- `data_layer/caching/infrastructure/flight.py` `StoreSingleFlight.release`: borra el lock sin
  verificar dueño (cálculo duplicado).
- `common/ids/uuid7.py`: con más de ~2000 ids por milisegundo el orden por id deja de ser
  temporal.
- `entrypoints/adapters/rpc` `RpcGateway.routes()`: no ve lo registrado después de construir.
- `data_layer/orm/sqlalchemy/adapters/runtime_use_cases.py` `SqlUseCases.save`: `max+1` sin
  lock; dos réplicas pueden tomar la misma posición.

**Inconsistencias entre implementaciones**
- `increment` y el TTL difieren por adapter (Redis lo conserva, memoria lo pierde, DynamoDB guarda
  el contador aparte del valor); `KeyValueContexts.delete` reinicia la versión y `InMemoryContexts`
  no; `InMemoryContexts` usa `datetime.now` en vez del reloj inyectado.
- REST lee `DELETE` del body y FastAPI de la URL; `MemoryRepository.search` con `for_update`
  bloquea todas las filas y no solo la página; `MemoryRepository._sorted` falla con `None`.
- `memcached` `take`/`replace(None)`: entre el `cas` y el `delete` la clave vale `b""`.

**Menores**
- Near-cache de `QueryCaching` sin límite; `StoreCircuitBreaker` sin half-open; `DynamoKeyValue.
  get_many` reintenta sin espera; `baggage` corta en la primera clave larga y descarta el resto;
  `ThreadContextBus` reetiqueta cualquier `RuntimeError`; gRPC health `Watch` cierra el stream;
  hooks `after_read`/`after_search` sin nivel HOOK; `introspection` choca con dos DTO del mismo
  nombre; `bounds_of` trata `>` como `>=`; `DataFrame.append` rechaza columnas en otro orden con un
  mensaje vacío; `BusRegistry.reload` ignora un cambio de orden; typo "rigistering" en
  `use_bus.py`; f-string anidado en `AccessControl.verify`.
