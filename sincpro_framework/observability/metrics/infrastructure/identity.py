"""Who a metric comes from — three levels, each where it belongs (PRD_03 §4.9).

    the service      the resource: service.name = the artifact, stable across releases;
                     service.version and tenant (`resource.tenant`, Sincpro's canonical key) beside it
    the context      sincpro.context on every series
    what runs it     sincpro.context.info{sincpro.context, sincpro.artifact, sincpro.version,
                     sincpro.tenant} = 1 — one series per bounded context, joined when asked

Context: traces keep the release in `service.name` (`sincpro-odoo:18.5.0-rc2`) because a trace is
looked up by release; a metric is read across releases, and a version in its job would start
every series again at each deploy — `rate()` breaks across it, dashboards need a regex. The
version, the tenant and the library behind each context travel once, on the info series, as a
Prometheus `*_build_info` does: `* on (sincpro_context) group_left (sincpro_version)`.
"""

import threading
from typing import Protocol
from weakref import WeakKeyDictionary

from sincpro_framework.observability.domain import ObservabilityIdentity, tenant
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder


class BusObservability(Protocol):
    """What the metrics ask of the bus that runs a use case — its `Observability`: who it is,
    and whether an error is traffic it was told to expect. Read only when something records,
    and every read is shielded: a bus that answers neither is measured all the same."""

    @property
    def identity(self) -> ObservabilityIdentity: ...

    def expects(self, error: BaseException) -> bool: ...


CONTEXT_INFO = Instrument(
    name="sincpro.context.info",
    kind=InstrumentKind.UP_DOWN,
    description="1 for each bounded context of the process: the library and version that run it",
    label_keys=("sincpro.context", "sincpro.artifact", "sincpro.version", "sincpro.tenant"),
)


def metrics_resource(identity: ObservabilityIdentity) -> dict[str, str]:
    """The resource of the process's meter provider: a stable service name, its version and
    tenant as attributes of their own — only what is set."""
    attributes = {"service.name": identity.service}
    if identity.service_version:
        attributes["service.version"] = identity.service_version
    if tenant():
        attributes["tenant"] = tenant()
    return attributes


_announced: "WeakKeyDictionary[Recorder, set[tuple[str, ...]]]" = WeakKeyDictionary()
_lock = threading.Lock()


def announce(recorder: Recorder, identity: ObservabilityIdentity, context: str) -> bool:
    """`sincpro.context.info` once per context and recorder — whichever recorder records, even
    one chosen after the bus was built. Answers whether it had to be announced now."""
    labels = {
        "sincpro.context": context,
        "sincpro.artifact": identity.service,
        "sincpro.version": identity.service_version,
        "sincpro.tenant": tenant(),
    }
    key = tuple(labels.values())
    with _lock:
        seen = _announced.setdefault(recorder, set())
        if key in seen:
            return False
        seen.add(key)
    recorder.add(CONTEXT_INFO, 1, labels)
    return True


def identity_of(who: BusObservability | None) -> ObservabilityIdentity | None:
    return getattr(who, "identity", None) if who is not None else None
