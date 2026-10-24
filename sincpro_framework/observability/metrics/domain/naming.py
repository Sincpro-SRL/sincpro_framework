"""How a declared metric is named — derived from the code, never written (PRD_03 §4.3).

    {context}.{use case}.runs             @metrics.counts            billing.issue_invoice.runs
    {context}.{use case}.{field}          @metrics.sums / .measures  billing.issue_invoice.total
    {context}.{use case}.{attribute}      metrics.counter() & co.    billing.issue_invoice.retries

Context: the bounded context comes from the bus that runs the use case, the use case from its
class, the rest from the field or the attribute it is declared on — so a metric moves with a
rename and two contexts never collide on one name.
"""

import re

_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake(name: str) -> str:
    """`IssueInvoice` → `issue_invoice`, `CreateQREconomico` → `create_qr_economico`,
    `doors-billing` → `doors_billing`."""
    return _BOUNDARY.sub("_", name).replace("-", "_").replace(" ", "_").lower()


def metric_name(context: str, use_case: type, *rest: str) -> str:
    """The dotted name of a metric of `use_case` on the bus `context` — `sincpro` when the
    use case runs on no bus.

        metric_name("billing", IssueInvoice, "total")   ->  "billing.issue_invoice.total"
        metric_name("", IssueInvoice, "runs")           ->  "sincpro.issue_invoice.runs"
    """
    return ".".join((snake(context or "sincpro"), snake(use_case.__name__), *rest))
