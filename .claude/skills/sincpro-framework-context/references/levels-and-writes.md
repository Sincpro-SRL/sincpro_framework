# The tree, navigation and the two writes

## The levels

| Level | Opened by | Holds |
|---|---|---|
| `ROOT` | the process, once | defaults (`lang`, `tz`, a default tenant), flags, settings and stores under their type |
| `BUS` | the first call or `bus.context(...)` that reaches a bus | what the bus publishes: `bus.context({...}, global_scope=True)`, `bus.context_store(store)` |
| `ENTRYPOINT` | REST, JSON-RPC, gRPC, MCP, a queue consumer, a cron, `remote_execution`, or the first `bus.context(...)` / `carrying(...)` of a flow (`DIRECT`) | who, for which tenant, lang, the identity — and `kind` |
| `APPLICATION` / `FEATURE` | every `execute`, by itself | what the handler `set`s; its `Execution` |
| `HOOK` | every run of a repository hook | what the hook `set`s |
| `SCOPE` | `with bus.context(...)` inside a flow, `with use_context().scoped(...)` | what the block changes for what it calls |

Interceptors run **inside** the node of the use case they wrap: a value an interceptor sets is the
handler's.

## Reading

```python
context = use_context()                     # or self.context in a handler
context["tenant_id"]                         # KeyError when nothing says it
context.get("lang", "es")
SiatSettings in context and context[SiatSettings]
dict(context) · json.dumps(context) · {**context}       # it is a dict

context.level                                # Level.FEATURE
context.entrypoint.kind                      # EntrypointKind.REST
use_context(Level.ENTRYPOINT)["user_id"]     # who asked, whatever a lower level did
context.origin("tenant_id")                  # Origin(level=Level.SCOPE, label="sales", execution_id=None)
[node.level for node in context.lineage()]   # FEATURE, APPLICATION, ENTRYPOINT, BUS, ROOT
context.own                                  # only this node's values
context.execution.execution_id               # the identity
```

A `Context` is the context **when it was asked for**. Ask again after a write, a scope or a
provider — the way a React hook is called where it is used.

## Writing

```python
context["note"] = "x"         # the scope of the call: every execution of this call sees it
del context["lang"]           # hidden in that scope only; the level above keeps it
context.update(a=1)           # same as [] =
context.set("pos_id", 3)      # this node: what it runs afterwards — not its caller, not a sibling
context.unset("pos_id")
context.entrypoint.set("lang", "en")    # the whole flow, on purpose
context.root.set("maintenance", True)   # the process, on purpose

with use_context().scoped({"tenant_id": "beta"}) as block:    # a child for the block
    billing(CommandIssueInvoice(...))
```

`self.context = {...}` replaces what the scope of the call says. `bus.current_context()` refuses every
write (`TypeError`).

## A block with only a parent level's context

```python
with use_context().parent.scoped():                  # without what this node changed
    self.feature_bus.execute(CommandAudit())
with use_context(Level.ENTRYPOINT).scoped({"lang": "en"}):   # exactly what the entrance knew, + lang
    self.feature_bus.execute(CommandNotify())
```

`scoped()` opens from any level; the block sees that level and stays in the execution in play (same
identity, same cause).

## Renaming the flow

Writing `correlation_id` (`[]=`, `set`, or a scope that names it) renames the flow from there on: the
running execution, its signals and what it runs afterwards say the new one; a scope's name ends with
the scope. `execution_id` / `causation_id` are minted by the framework — written mid-execution they are
warned about and only an entrance's are taken.

## The standard keys

| Key | Meaning |
|---|---|
| `user_id` | who the execution acts for (auth sets it) |
| `tenant_id` | the tenant it acts in |
| `tenant_ids` | every tenant it may act across (Odoo's `allowed_company_ids`) |
| `correlation_id` · `causation_id` · `execution_id` | the identity |

`"user.id"` and `"tenant"` are read as the standard keys with a warning the first time. Logs, spans
and metrics keep their names: `tenant`, `user_id`.
