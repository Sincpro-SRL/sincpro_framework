"""What observability needs to know, as data. No backend imported here.

Two things every backend asks for and used to resolve on its own: **who is
emitting** (``ObservabilityIdentity``) and **whether a backend came up**
(``ComponentStatus``). Resolving them once, here, is what keeps the GlitchTip
release and the Tempo ``service.name`` from drifting apart.
"""

import inspect
from importlib.metadata import packages_distributions
from importlib.metadata import version as distribution_version
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict

from sincpro_framework.sincpro_conf import settings

UNKNOWN = "unknown"
FRAMEWORK_ARTIFACT = "sincpro-framework"
DEFAULT_BUS = "sincpro_framework"

State = Literal["off", "on", "failed"]

_distributions: Mapping[str, list[str]] | None = None


def _slug(value: str) -> str:
    return (value or "").strip().replace("-", "_")


class ObservabilityIdentity(BaseModel):
    """Who is emitting: the deployed artifact, its version, and the bus inside it."""

    model_config = ConfigDict(frozen=True)

    artifact: str = UNKNOWN
    version: str = UNKNOWN
    bus: str = DEFAULT_BUS

    @property
    def service_name(self) -> str:
        """Tempo ``service.name``: ``artifact:version:bus``. The bus is never dropped."""
        return f"{_slug(self.artifact)}:{_slug(self.version)}:{_slug(self.bus)}"

    @property
    def release(self) -> str:
        """GlitchTip release: ``artifact:version`` — literally ``APP_RELEASE``.

        For an SDK it is the distribution and its version. The bus is not part of
        the release: it travels as the ``sincpro.instance`` tag, because two buses
        of one deployment ship the same release. Only when there is no artifact at
        all does the bus stand in for it, so events stay separable per context.
        """
        artifact = self.artifact if self.artifact != UNKNOWN else self.bus
        return f"{artifact}:{self.version}"


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


def _from_argument(package: str, version: str) -> tuple[str, str]:
    artifact = (package or "").strip()
    resolved = (version or "").strip()
    if artifact and not resolved:
        resolved = installed_version(artifact)
    return artifact, resolved


def _from_app_release() -> tuple[str, str]:
    """``APP_RELEASE`` is the Sincpro standard on every deployed service."""
    release = (settings.app_release or "").strip()
    if not release:
        return "", ""
    if ":" in release:
        artifact, version = release.rsplit(":", 1)
        return artifact.strip(), version.strip()
    return "", release


def _from_env_service_name() -> tuple[str, str]:
    return (settings.otel_service_name or "").strip(), ""


def _from_caller_distribution() -> tuple[str, str]:
    """The installed distribution of the first caller outside the framework.

    This is the library case — an SDK creating a bus inside a host that is not
    itself a distribution. Only reached when nothing cheaper answered.
    """
    try:
        for frame_info in inspect.stack(0)[1:]:
            module = inspect.getmodule(frame_info.frame)
            if module is None or not module.__name__:
                continue
            name = module.__name__
            if name == "sincpro_framework" or name.startswith("sincpro_framework."):
                continue
            distributions = _import_to_distribution().get(name.split(".")[0]) or []
            if not distributions:
                return "", ""
            artifact = distributions[0]
            return artifact, installed_version(artifact)
    except Exception:
        pass
    return "", ""


def resolve_identity(bus: str, package: str = "", version: str = "") -> ObservabilityIdentity:
    """Resolve who is emitting, cheapest and most explicit source first.

    1. What the caller passed to ``UseFramework(package=..., version=...)``.
    2. ``APP_RELEASE`` — always set on Sincpro services.
    3. ``OTEL_SERVICE_NAME`` — names the artifact when only a version is known.
    4. The caller's installed distribution — the library case, and the slow one.
    Final: anything still missing becomes ``unknown``; the bus segment always survives.
    """
    artifact, resolved = _from_argument(package, version)

    for source in (_from_app_release, _from_env_service_name, _from_caller_distribution):
        if artifact and resolved:
            break
        found_artifact, found_version = source()
        artifact = artifact or found_artifact
        resolved = resolved or found_version

    return ObservabilityIdentity(
        artifact=artifact or UNKNOWN,
        version=resolved or UNKNOWN,
        bus=bus or DEFAULT_BUS,
    )
