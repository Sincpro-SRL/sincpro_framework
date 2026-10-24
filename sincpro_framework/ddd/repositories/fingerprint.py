"""The fingerprint of a read: one key for every read that answers the same rows, whatever page it
asks for — so what was read once is never read again.

Context: it is taken after the filter's values are read as their fields' types, so `"1000"` and
`1000` are one key, and a `today` or a user is read first, so the key is of the rows it asked
for and never of the words it was written with; the parts of an `all` or an `any`, and the items
of an `in`, are keyed in any order; a decimal is keyed by its value, not by how many zeros it was
written with (`criteria/canonical.py`). The order and
the mask are part of it — they change which rows a page holds and what each row carries — and
so is the scope a repository reads under, so two tenants never share a key. The pagination is
not: a later page is the same read, continued.
"""

import hashlib
import json

from sincpro_framework.ddd.criteria import Criteria, ResolutionContext, canonical
from sincpro_framework.ddd.entity.entity_meta import describe_class


def fingerprint_of(
    model: type,
    criteria: Criteria,
    scope: Criteria | None = None,
    resolution: ResolutionContext | None = None,
) -> str:
    """The key of one read of `model`: 32 hex characters, the same for every page of it.

        fingerprint_of(Invoice, Criteria(where=Condition(field="total", value="100")))
        ==  fingerprint_of(Invoice, Criteria(where=Condition(field="total", value=100),
                                             pagination={"limit": 10}))

    1. The filter accepted by the model — values read as their fields' types, dynamic
       values resolved against `resolution` — then put in its canonical form.
    2. The scope, when the repository reads under one, the same way.
    3. Final: the aggregate's qualified name, filter, scope, order and specification as
       sorted JSON, hashed with SHA-256 and cut to 32 characters; the pagination left out.
    """
    meta = describe_class(model)
    where = canonical(meta.accept(criteria.expression, resolution)[0])
    scoped = (
        canonical(meta.accept(scope.expression, resolution)[0]) if scope is not None else None
    )
    dumped = criteria.model_dump(mode="json")
    body = {
        "aggregate": f"{model.__module__}.{model.__qualname__}",
        "where": where and where.model_dump(mode="json"),
        "scope": scoped and scoped.model_dump(mode="json"),
        "order": dumped["order"],
        "specification": dumped["specification"],
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]
