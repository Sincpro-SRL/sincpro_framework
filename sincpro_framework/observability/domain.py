"""What observability needs to know, as data. No backend imported here.

Two things every backend asks for and used to resolve on its own: **who is
emitting** (``ObservabilityIdentity``) and **whether a backend came up**
(``ComponentStatus``). Resolving them once, here, is what keeps the GlitchTip
release and the Tempo ``service.name`` from drifting apart.
"""

import inspect
import os
from functools import cache
from importlib.metadata import distribution, packages_distributions
from importlib.metadata import version as distribution_version
from importlib.util import find_spec
from pathlib import Path
from typing import Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict

from sincpro_framework.sincpro_conf import settings

UNKNOWN = "unknown"
FRAMEWORK_ARTIFACT = "sincpro-framework"
DEFAULT_BUS = "sincpro_framework"

OUTCOME = "sincpro.outcome"
"""How a run ended — the same key on the metrics, the span, the log line and GlitchTip."""
OK = "ok"
EXPECTED = "expected"

State = Literal["off", "on", "failed"]

_distributions: Mapping[str, list[str]] | None = None


class ObservabilityIdentity(BaseModel):
    """Who is emitting: the deployed artifact, its version, and the bus inside it."""

    model_config = ConfigDict(frozen=True)

    artifact: str = UNKNOWN
    version: str = ""
    bus: str = DEFAULT_BUS

    @property
    def service_name(self) -> str:
        """Tempo ``service.name`` and the logs' ``service_name``: the artifact, stable across
        releases — ``sincpro-siat-soap``, ``sincpro_odoo_mcp`` out of
        ``registry.digitalocean.com/sincpro/sincpro_odoo_mcp:0.8.0``.

        Context: the version and the bus used to be part of it
        (``sincpro-siat-soap:8.0.4:siat-soap-sdk``), so every release and every bounded
        context was a service of its own. The version is ``service.version`` now and the
        bus is ``sincpro.context`` and the span's ``context/DTO`` name. Only when no
        artifact resolves does the bus stand in, so buses stay separable. Values are never
        normalized: ``-`` stays ``-``.
        """
        return self.service if self.artifact != UNKNOWN else self.bus

    @property
    def release(self) -> str:
        """GlitchTip release: literally ``APP_RELEASE``, or ``distribution:version``.

        The bus is not part of it — two buses of one deployment ship the same
        release and are told apart by the ``sincpro.instance`` tag. Only when there
        is no artifact at all does the bus stand in, so events stay separable.
        """
        artifact = self.artifact if self.artifact != UNKNOWN else self.bus
        return ":".join(part for part in (artifact, self.version) if part)

    @property
    def service(self) -> str:
        """The artifact without its version or its registry — stable across releases.
        `APP_RELEASE` arrives whole, often as an image reference
        (`registry.example.com/team/sincpro-odoo:18.5.0-rc2`, kept verbatim for the GlitchTip
        release): the name is `sincpro-odoo`. A library comes as name and version apart."""
        return self._split()[0].rsplit("/", 1)[-1]

    @property
    def service_version(self) -> str:
        return self._split()[1]

    def _split(self) -> tuple[str, str]:
        """Name and version out of a release, read as an image reference or ``name@version``.

        1. A version given apart (a library's) is kept as it is.
        2. ``@``: a digest (``@sha256:…``) is dropped; anything else is the version.
        3. ``:`` after the last ``/`` is the tag; one before it is a registry's port.
        """
        artifact, version = self.artifact, self.version
        if version:
            return artifact, version
        if "@" in artifact:
            artifact, after = artifact.rsplit("@", 1)
            if ":" not in after:
                return artifact, after
        colon = artifact.rfind(":")
        if colon > artifact.rfind("/"):
            return artifact[:colon], artifact[colon + 1 :]
        return artifact, ""


def tenant() -> str:
    """Which tenant the deployment serves — `TENANT`, else `tenant` in `OTEL_RESOURCE_ATTRIBUTES`;
    empty when neither says one. The resource's `tenant`, the GlitchTip environment and
    `sincpro.tenant`; an execution's own is `correlation.tenant()` (PRD_03 §4.10)."""
    from sincpro_framework.observability.correlation import deployment_tenant

    return deployment_tenant()


def declared_exporters(value: str | None) -> set[str]:
    """`OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER` as OpenTelemetry defines them: a comma
    list (`otlp`, `prometheus`, `none`, `console`); empty when not set."""
    return {part.strip().lower() for part in (value or "").split(",") if part.strip()}


def otel_sdk_disabled() -> bool:
    """`OTEL_SDK_DISABLED=true`: no OpenTelemetry signal of the framework's own."""
    return bool(getattr(settings, "otel_sdk_disabled", False))


def declared_resource_keys() -> set[str]:
    """The keys the deployment itself put in `OTEL_RESOURCE_ATTRIBUTES` — what it declares wins
    over what the framework derives (a host such as Odoo sets `tenant` there)."""
    declared = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    return {pair.split("=", 1)[0].strip() for pair in declared.split(",") if "=" in pair}


def describing_attributes(identity: ObservabilityIdentity) -> dict[str, str]:
    """`service.version`, `release` and the deployment's `tenant` for a resource, leaving out
    what the deployment declared.

    Context: `Resource.create` puts explicit attributes over `OTEL_RESOURCE_ATTRIBUTES`; left
    in, a derived value would silently replace the one the operator chose.
    """
    declared = declared_resource_keys()
    attributes: dict[str, str] = {}
    if identity.service_version and "service.version" not in declared:
        attributes["service.version"] = identity.service_version
    if identity.artifact != UNKNOWN and identity.release and "release" not in declared:
        attributes["release"] = identity.release
    deployed = tenant()
    if deployed and "tenant" not in declared:
        attributes["tenant"] = deployed
    return attributes


def log_fields(identity: ObservabilityIdentity) -> dict[str, str]:
    """Who a log line comes from and for whom, on the line itself: the same release, service,
    version and tenant the spans, the metrics and GlitchTip carry — readable without the
    collector's labels. The tenant and the user are the execution's (PRD_03 §4.10)."""
    from sincpro_framework.observability.correlation import tenant as execution_tenant
    from sincpro_framework.observability.correlation import user_id

    fields = {"service_name": identity.service_name}
    if identity.service_version:
        fields["service_version"] = identity.service_version
    if identity.artifact != UNKNOWN and identity.release:
        fields["release"] = identity.release
    who = execution_tenant()
    if who:
        fields["tenant"] = who
    user = user_id()
    if user:
        fields["user_id"] = user
    return fields


class ComponentStatus(BaseModel):
    """Whether one backend came up, and why not when it didn't."""

    active: bool = False
    state: State = "off"
    reason: str = "not_built"


class ObservabilityStatus(BaseModel):
    """Probe of both backends, read from ``framework.observability.status``."""

    sentry: ComponentStatus = ComponentStatus()
    otel: ComponentStatus = ComponentStatus()


def on(reason: str) -> ComponentStatus:
    return ComponentStatus(active=True, state="on", reason=reason)


def off(reason: str) -> ComponentStatus:
    return ComponentStatus(active=False, state="off", reason=reason)


def failed(reason: str) -> ComponentStatus:
    return ComponentStatus(active=False, state="failed", reason=reason)


def failure_of(exc: BaseException) -> ComponentStatus:
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) > 200:
        text = text[:197] + "..."
    return failed(text)


def installed_version(distribution: str) -> str:
    """Version of an installed distribution, or ``""`` when it is not one."""
    try:
        return distribution_version(distribution)
    except Exception:
        return ""


@cache
def framework_version() -> str:
    """Read once: it is asked on every span, and the metadata lookup behind it is not free."""
    return installed_version(FRAMEWORK_ARTIFACT) or UNKNOWN


def framework_identity(bus: str = DEFAULT_BUS) -> ObservabilityIdentity:
    """Identity of errors raised by the framework itself, not by a bounded context.

    The bus is kept so the event still says which one hit it; only the artifact and
    version change, because the bug belongs to the framework's release.
    """
    return ObservabilityIdentity(
        artifact=FRAMEWORK_ARTIFACT, version=framework_version(), bus=bus or DEFAULT_BUS
    )


def _import_to_distribution() -> Mapping[str, list[str]]:
    """``packages_distributions()`` costs ~200ms and never changes within a process."""
    global _distributions
    if _distributions is None:
        try:
            _distributions = packages_distributions()
        except Exception:
            _distributions = {}
    return _distributions


def _ships_the_imported(artifact: str, top: str) -> bool:
    """Whether the ``top`` package Python actually imports is the one this distribution
    installed, and not a namesake.

    A distribution can ship a generic top-level name: ``caio`` installs a ``tests/``
    package, and from then on the mapping claims every project's own ``tests`` as caio's.
    Where the imported package really lives settles it. When either side cannot be
    located, the mapping is trusted as it was.
    """
    try:
        spec = find_spec(top)
        if spec is None:
            return True
        if spec.submodule_search_locations:
            imported = Path(next(iter(spec.submodule_search_locations)))
        elif spec.origin:
            imported = Path(spec.origin).parent
        else:
            return True
        installed = Path(str(distribution(artifact).locate_file(top)))
        return imported.resolve() == installed.resolve()
    except Exception:
        return True


def _from_deployment() -> tuple[str, str]:
    """``APP_RELEASE``, the Sincpro standard on services. The service case.

    Taken verbatim: it always ships with its version, so there is nothing to
    split — and nothing to mangle on a release that is not ``name:version``.
    ``OTEL_SERVICE_NAME`` only names the deployment when ``APP_RELEASE`` is absent.
    """
    release = (settings.app_release or "").strip()
    if release:
        return release, ""
    return (settings.otel_service_name or "").strip(), ""


def caller_module() -> str:
    """Module of the first frame outside the framework — whoever built the bus.

    Must be read while that frame is still on the stack (at ``UseFramework``
    construction). Resolved later, from a request, the stack belongs to the host
    application and this would name Odoo instead of the library.
    """
    try:
        for frame_info in inspect.stack(0)[1:]:
            module = inspect.getmodule(frame_info.frame)
            if module is None or not module.__name__:
                continue
            name = module.__name__
            if name == "sincpro_framework" or name.startswith("sincpro_framework."):
                continue
            return name
    except Exception:
        pass
    return ""


def _installed_distribution_of(module_name: str) -> tuple[str, str]:
    """``(distribution, version)`` of a module, by the ``_`` → ``-`` convention.

    Costs ~0.7ms, against ~200ms for the full mapping below, and covers every
    Sincpro package: ``sincpro_siat_soap`` ships as ``sincpro-siat-soap``.
    """
    top = (module_name or "").split(".")[0]
    if not top:
        return "", ""
    for candidate in (top.replace("_", "-"), top):
        version = installed_version(candidate)
        if version:
            return candidate, version
    return "", ""


def _from_distribution_scan(module_name: str) -> tuple[str, str]:
    """A distribution whose import name differs from its package name.

    ``packages_distributions()`` costs ~200ms and does not cache on its own, so
    this runs only when neither the naming convention nor the deployment knew.
    """
    top = (module_name or "").split(".")[0]
    if not top:
        return "", ""
    try:
        distributions = _import_to_distribution().get(top) or []
    except Exception:
        return "", ""
    for artifact in distributions:
        if _ships_the_imported(artifact, top):
            return artifact, installed_version(artifact)
    return "", ""


def _from_caller_library(module_name: str) -> tuple[str, str]:
    """The installed library that created the bus. The library case."""
    return _installed_distribution_of(module_name)


def _identity_sources(module_name: str) -> Iterator[tuple[str, str]]:
    """Sources in order, evaluated one at a time.

    A generator on purpose: building this as a tuple would run every source —
    including the ~200ms scan — before the first one had a chance to answer.
    """
    yield _from_caller_library(module_name)
    yield _from_deployment()
    yield _from_distribution_scan(module_name)


def resolve_identity(
    bus: str, package: str = "", version: str = "", module_name: str = ""
) -> ObservabilityIdentity:
    """Resolve who is emitting. The first source that names an artifact wins.

    1. What the caller declared explicitly — rarely used, an escape hatch.
    2. The library that built the bus, by the module → distribution convention.
    3. The deployment: ``APP_RELEASE``, named by ``OTEL_SERVICE_NAME`` when needed.
    4. A distribution whose import name differs from its package name (~200ms).
    Final: a source contributes its artifact **and its own version** — never one
    paired with another source's, which is how a library inside Odoo used to end up
    reporting Odoo's name against the library's version.
    """
    explicit_artifact = (package or "").strip()
    explicit_version = (version or "").strip()

    if explicit_artifact:
        return ObservabilityIdentity(
            artifact=explicit_artifact,
            version=explicit_version or installed_version(explicit_artifact),
            bus=bus or DEFAULT_BUS,
        )

    for artifact, resolved in _identity_sources(module_name):
        if artifact:
            return ObservabilityIdentity(
                artifact=artifact,
                version=explicit_version or resolved,
                bus=bus or DEFAULT_BUS,
            )

    return ObservabilityIdentity(version=explicit_version, bus=bus or DEFAULT_BUS)
