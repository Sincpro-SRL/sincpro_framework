"""What observability needs to know, as data. No backend imported here.

Two things every backend asks for and used to resolve on its own: **who is
emitting** (``ObservabilityIdentity``) and **whether a backend came up**
(``ComponentStatus``). Resolving them once, here, is what keeps the GlitchTip
release and the Tempo ``service.name`` from drifting apart.
"""

import inspect
from importlib.metadata import packages_distributions
from importlib.metadata import version as distribution_version
from typing import Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict

from sincpro_framework.sincpro_conf import settings

UNKNOWN = "unknown"
FRAMEWORK_ARTIFACT = "sincpro-framework"
DEFAULT_BUS = "sincpro_framework"

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
        """Tempo ``service.name``: ``artifact:version:bus``, empty segments omitted.

        A service is identified by ``APP_RELEASE``, which already carries its
        version, so it has no separate version segment:
        ``sincpro-odoo:18.5.0-rc2:common-mcp``. A library contributes name and
        version apart: ``sincpro-siat-soap:8.0.3:siat-soap-sdk``.

        Values travel verbatim. Rewriting ``-`` to ``_`` used to mangle both the
        distribution name you would search for and the version itself
        (``18.5.0-rc2`` became ``18.5.0_rc2``).
        """
        return ":".join(part for part in (self.artifact, self.version, self.bus) if part)

    @property
    def release(self) -> str:
        """GlitchTip release: literally ``APP_RELEASE``, or ``distribution:version``.

        The bus is not part of it — two buses of one deployment ship the same
        release and are told apart by the ``sincpro.instance`` tag. Only when there
        is no artifact at all does the bus stand in, so events stay separable.
        """
        artifact = self.artifact if self.artifact != UNKNOWN else self.bus
        return ":".join(part for part in (artifact, self.version) if part)


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


def framework_version() -> str:
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


def _from_caller_library(module_name: str) -> tuple[str, str]:
    """The installed library that created the bus. The library case."""
    return _installed_distribution_of(module_name)


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
    if not distributions:
        return "", ""
    artifact = distributions[0]
    return artifact, installed_version(artifact)


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
