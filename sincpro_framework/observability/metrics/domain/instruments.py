"""What a metric is, as data — the same whatever records it (PRD_03 §4.1).

Context: an instrument is described once and handed to a `Recorder` with every measurement; the
recorder creates its own object for it (a Prometheus `Counter`, an OTel histogram) the first time
it sees the name. Nothing here knows a backend.
"""

from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject

SECONDS = "s"
"""Durations are always in seconds — what makes histograms of different services aggregate."""

DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
)
"""OpenTelemetry's for `http.server.request.duration`, in seconds. Every duration carries them:
OTel's own default (0, 5, 10, 25 … 10000) is sized for milliseconds and would put every run
in its first bucket."""


class InstrumentKind(StrEnum):
    COUNTER = "counter"
    """Only goes up: runs, amounts issued. A negative value is dropped."""
    UP_DOWN = "up_down"
    """Goes up and down: items in flight, a queue's depth."""
    HISTOGRAM = "histogram"
    """A distribution: durations, amounts per invoice — rate, percentiles and sum from one."""


class Instrument(DataTransferObject):
    """One metric: its name, what it counts in, and the labels every measurement carries."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Dotted and lowercase (`billing.issue_invoice.total`); a recorder translates it for its
    backend (`billing_issue_invoice_total_total` on Prometheus)."""
    kind: InstrumentKind
    unit: str = ""
    """UCUM, as OpenTelemetry: `s`, `By`, `BOB`, `{invoice}`; metadata, never in the name."""
    description: str = ""
    label_keys: tuple[str, ...] = ()
    """Fixed for the life of the instrument — Prometheus refuses a label it was not created with."""
    buckets: tuple[float, ...] | None = None
    """A histogram's bucket boundaries; `None` is the recorder's default."""
