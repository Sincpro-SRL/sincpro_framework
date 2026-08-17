"""Observability probe: optional backends, never raise, always inspectable."""

from typing import Literal, TypedDict

ObservabilityState = Literal["off", "on", "failed"]


class ComponentStatus(TypedDict):
    active: bool
    state: ObservabilityState
    reason: str


class ObservabilityStatus(TypedDict):
    sentry: ComponentStatus
    otel: ComponentStatus


def component_status(active: bool, state: ObservabilityState, reason: str) -> ComponentStatus:
    """Build a component probe. Callers never raise on observability."""
    return {"active": active, "state": state, "reason": reason}


def status_from_exception(exc: BaseException) -> ComponentStatus:
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) > 200:
        text = text[:197] + "..."
    return component_status(False, "failed", text)


def not_built_status() -> ObservabilityStatus:
    return {
        "sentry": component_status(False, "off", "not_built"),
        "otel": component_status(False, "off", "not_built"),
    }
