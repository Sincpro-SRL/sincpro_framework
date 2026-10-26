"""What a use case declares it measures — pure data, read by the bus on every run (PRD_03 §4.2).

Context: a declaration names its value and its labels by field reference
(`field_path(Command).field`), so what it reads is checked when it is written; the bus reads
them off the Command and the Response of a successful run. A failed run is already measured,
by kind, by the metric every use case gets.
"""

from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.observability.field_paths import FieldPath
from sincpro_framework.sincpro_abstractions import DataTransferObject


class MetricDecorator(StrEnum):
    """Which of the three decorators declared it — what the bus does with the run's value."""

    COUNTS = "counts"
    """One more for each successful run."""
    SUMS = "sums"
    """The value of a field, added up — amounts, lines, units."""
    MEASURES = "measures"
    """The value of a field, as a distribution — percentiles and averages of it."""


class MetricDeclaration(DataTransferObject):
    """One metric a use case declared with a decorator — what it measures, read off which
    field, by which labels; its name is derived when the bus first records it.

        @metrics.sums(field_path(ResponseIssueInvoice).total,
                      by=field_path(CommandIssueInvoice).currency)
            ->  MetricDeclaration(measure=SUMS, value=field_path(ResponseIssueInvoice).total,
                            labels=(field_path(CommandIssueInvoice).currency,))
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    measure: MetricDecorator
    value: FieldPath | None = None
    """What `sums` or `measures` reads; `None` for `counts`."""
    labels: tuple[FieldPath, ...] = ()
    unit: str = ""
    description: str = ""
    buckets: tuple[float, ...] | None = None
