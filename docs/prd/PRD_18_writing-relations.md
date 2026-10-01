# PRD_18: Writing relations — sets you add to, take from and replace (SQLAlchemy adapter)

- **Status**: specification, to think over. Nothing here is built. Open decisions are at the end.
- **Depends on**: PRD_17 (the cascade, `Held`, `save` as the one door), the relation descriptors
  (`orm/sqlalchemy/services/data_mapper.py`), `EntityCollection` and its set algebra
  (`ddd/entity/entity_collection.py`), the resolver (`orm/sqlalchemy/services/relation_resolver.py`).
- **Scope**: the SQLAlchemy `Repository`. The relations are the framework's own descriptors,
  never SQLAlchemy `relationship()`; every write below is planned before the flush and handed to
  the path every aggregate takes (PRD_17 §2).
- **Philosophy**: a relation is read and written the same way, whatever its kind. What the domain
  does to it — add, take away, replace — is an intention the relation remembers, and `save`
  carries it out in the transaction. What cannot be honoured is refused, never ignored.

## Problem

Reading a relation is symmetric across its kinds; writing one is not. Measured on the current
code:

| What the domain does | Today |
|---|---|
| `ws.repositories.append(x)` on a loaded relation | no such method: a loaded to-many is an immutable `EntityCollection` |
| `ws.repositories.append(x)` on the list a constructor gave | written — it is a plain `list` |
| `ws.repositories.remove(x)` on that list | **ignored**: `x` stays in the database |
| `ws.repositories = [*ws.repositories, x]` | written, but it is a replacement that has to read the whole set |
| `repo.workspace = other` (a to-one) | **ignored**: the foreign key keeps its old value |
| `invoice.tags = [...]` (a many-to-many) | never written |
| anything on a relation another context answers | silently kept in memory only |

The same field is a `list` when built and an `EntityCollection` when loaded, so the type checker
knows neither `-=` nor `.remove()` on it.

## Background — what mature ORMs settled on

| ORM | Add | Take away | Replace | To-one |
|---|---|---|---|---|
| **Odoo** | `rec.tag_ids += tag` · command `(4, id)` | `-= tag` · `(3, id)` unlink, `(2, id)` delete | `= recs` · `(6, 0, ids)` | `rec.partner_id = p` writes the column |
| **EF Core** | `author.Works.Add(w)` | `.Remove(w)` — required: delete, optional: NULL | assign a new collection | navigation assignment sets the FK |
| **Hibernate / JPA** | `children.add(c)` | `children.remove(c)` + `orphanRemoval` | `clear()` + `addAll()` | `child.setParent(p)` |
| **Rails** | `author.works << w` | `author.works.delete(w)` (`dependent:` decides) | `author.works = [...]` | `work.author = a` |
| **Ecto** | `put_assoc` | `on_replace: :delete \| :nilify` | `put_assoc` with the whole list | `put_assoc` |

All of them write the to-one's foreign key from the object, and separate one-to-many (taking a
child away deletes or detaches it) from many-to-many (taking a tag away unlinks a pair, the tag
lives on).

## The specification

The words **MUST**, **MUST NOT**, **SHOULD** and **MAY** are used as in RFC 2119.

### 1. Terms

- **Relation value**: what reading `record.relation` answers.
- **Intention**: what the domain did to a relation value since the last write — added these,
  took away those, or replaced the whole set.
- **Carried out**: the intention written by a `save`, after which the relation holds no intention.

### 2. One type for a to-many

A to-many's value MUST be an `EntityCollection[T]` whatever it came from — loaded, resolved by a
specification, or given to a constructor. A `list` handed to a constructor or assigned is wrapped.
Aggregates SHOULD annotate it as such, so the type checker knows the operators:

```python
@dataclass
class Workspace(Entity):
    repositories: EntityCollection[CodeRepo] = field(default_factory=EntityCollection)
```

`list[CodeRepo]` stays accepted: the value is wrapped all the same, and only the typing of `-=`
and `.remove()` is lost.

A relation value records intentions; a collection answered by `search()` MUST NOT — it stays
immutable, so `page -= x` answers a new collection and writes nothing.

### 3. The operators, and how Python reaches them

`a.works += [w]` runs as `tmp = a.works; tmp = tmp.__iadd__([w]); a.works = tmp`: every compound
operator ends in an assignment. The descriptor tells the two apart by what it is handed:

| Handed to `__set__` | Read as |
|---|---|
| the relation value itself, after `+=`, `\|=`, `-=` | the intention it recorded (add / take away) |
| a collection derived from it — `a.works \| [w]`, `a.works - [w]` | the same intention, carried by the result |
| anything else — a list, a new collection | a replacement of the whole set (PRD_17 §2.2) |

| Operation | Means | Named method |
|---|---|---|
| `+=`, `\|=` | add, by identity, no repeats | `.add(*records)` |
| `-=` | take away; a record not held is a no-op | `.remove(*records)` |
| `=` | replace the whole set | — |

Adding a record already held, or taking away one not held, changes nothing. Adding then taking
away the same record before a save leaves no intention.

### 4. many2one — the foreign key follows the object

`record.parent = other` MUST set the field the relation reads (`parent_field` of PRD's `key_pair`,
`workspace_id` by identity or `workspace_code` by a business key) to `other`'s key, on assignment,
so it is written by the next `save` with the record's own columns. `record.parent = None` sets it
to NULL, refused as a `ContractViolation` when the column is `NOT NULL`. Assigning a record whose
key is not set yet — a new parent — sets the field once the parent is written in the same `save`.

Nothing of `other` is written: a child does not own its parent.

### 5. one2many — the child carries the key

| Intention | `save(root)` writes |
|---|---|
| add `c` | `c` with the root's key; inserted if new, updated if moved from another root |
| take away `c` | `c` settled as an orphan (PRD_17 §2.3): `NOT NULL` key deletes it, nullable sets it to NULL |
| replace | the PRD_17 rules: what the last whole reading saw and the set no longer holds is settled |

A take-away is explicit, so it needs no reading: it works on a page, on a filtered reading and
outside `context()`. Moving a child is a take-away from one root and an add to another, and MUST
be saved in one call — `save([old_root, new_root])` — as PRD_17 already requires.

`owned=False` keeps today's meaning: the relation is a reference; `+=`, `-=` and `=` on it are
refused, because the root never writes it.

### 6. many2many — the pair, never the record

| Intention | `save(root)` writes in the table in between |
|---|---|
| add `t` | `INSERT (root_key, t_key)` — `t` itself is written only if new |
| take away `t` | `DELETE` of that pair — `t` is never deleted |
| replace | the pairs the last whole reading saw and the set no longer holds are deleted; new ones inserted |

Pairs are inserted in one statement per relation for the batch and deleted in one; a pair already
present is not inserted twice (`ON CONFLICT DO NOTHING` where the dialect has it, a read first
where it does not).

### 7. id_list — the column holds the set

`+=`, `-=` and `=` rewrite the JSON column of ids with the root's own columns. The related records
are never written.

### 8. A relation another context answers is read-only

A relation declared with `Relation.resolved_by(...)` or `Relation.bus(...)` lives somewhere this
repository cannot write. `+=`, `-=` and `=` on it MUST raise `ContractViolation` naming the
relation, instead of keeping a change no store will ever see.

### 9. How it meets `save`

The intentions join PRD_17's provenance (`Held`) instead of replacing it:

1. A relation with additions or take-aways and no replacement is carried out as such — no reading
   needed, no orphan inferred from a diff.
2. A replacement keeps PRD_17 §2.2: settled against what the last whole reading saw; a blind one
   saves and logs.
3. Final: once written, the relation holds no intention, and what it holds is what was read.

Hooks, the version check, stamping and change tracking apply to every record written, as for any
`save`. Nothing is written without a `save`.

### 10. The port and the double

Put to the manifesto's three questions (rule 3):

| Word | Same meaning everywhere | `MemoryRepository` | Goes to |
|---|---|---|---|
| a to-many is an `EntityCollection` with `+=`, `-=`, `=` | yes — a set is a set | holds it inside the root object | `ddd` (the collection is vocabulary) |
| a to-one assignment moves the foreign key | yes | sets the field on the object | `ddd` |
| what each intention becomes in SQL | no — rows, a table in between, a JSON column | — | the SQLAlchemy adapter |
| writing a cross-context relation is refused | yes | refuses too | both |

### 11. Conformance

On SQLite and Postgres, per kind: add, take away, replace, add-then-remove, a take-away on a page
and outside `context()`, a move between roots in one save and in two, a many-to-many take-away
leaving the tag, a to-one set to `None` on a `NOT NULL` column, a write on a bus relation refused,
and the statements counted for a batch of a hundred roots.

## Decisions

### The intention, not the diff

A diff of "what is held now" against "what is stored" is what deletes rows nobody asked to delete
(PRD_17 decisions §20). An add or a take-away says exactly which record, so it is carried out
without reading and without risk; a replacement stays the one operation that needs a whole reading.

### Methods first, operators as Odoo's sugar

Named methods are the mainstream: EF Core `Add`/`Remove`, Hibernate `add`/`remove`, SQLAlchemy
`append`/`remove`, Django `.add()`/`.remove()`/`.set()`. They read best in a domain method and are
what a reader new to the codebase looks for, so they are what the documentation shows. Odoo is the
precedent for `+=` / `-=`, and what the team writes every day, so they are offered as the same
intention. Python makes one rule non-negotiable: `obj.attr += x` always ends in `__set__`, so a
`__set__` handed the relation value itself is a no-op that keeps its recorded intention, and only a
different object is a replacement. On a many-to-many, `-=` unlinks the pair — deleting the tag is a
separate, explicit operation, as Odoo's `UNLINK` against `DELETE` and Rails' `delete` against
`destroy`.

## Open questions

1. ~~What a to-many inferred from a foreign key is by default.~~ *A part of its root: written and
   removed with it; a reference says `owned=False` (PRD_17 §2.1, decisions §20).*
2. ~~The commit writes unsaved changes.~~ *Only what was saved (decisions §25); an intention
   recorded and never saved is put back with the rest and named in the log.*
3. **`list[T]` annotations** — keep accepting and wrapping them, or require `EntityCollection[T]`
   so every relation is typed for its operators?
4. **`+` on a relation** — Odoo's `+` concatenates and `|` unions; here both would be a union by
   identity. Is that surprising enough to leave `+` out?
