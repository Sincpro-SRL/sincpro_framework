"""Bring GlitchTip up for one bus. Never raises. Never calls ``sentry_sdk.init()``.

The client is isolated on purpose: calling ``init()`` would replace the client the
host (Odoo, FastAPI) already installed, and the host's own errors would start
reporting under sincpro's release.
"""

import inspect
from typing import Any, Optional

from sincpro_framework.observability.domain import (
    ComponentStatus,
    ObservabilityIdentity,
    failed,
    failure_of,
    off,
    on,
    tenant,
)
from sincpro_framework.observability.registry import registry
from sincpro_framework.sincpro_conf import settings

try:
    import sentry_sdk  # pyright: ignore[reportMissingImports]

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


def dsn() -> str:
    """DSN from conf. Empty when absent or not a usable GlitchTip URL.

        sentry_dsn=" https://key@glitchtip.example.com/3 "
                        ->  "https://key@glitchtip.example.com/3"
        sentry_dsn="glitchtip.example.com/3"   ->  ""        # no scheme, no key

    It must start with `http://` or `https://` and carry a key (`@`); spaces stripped.
    """
    try:
        raw = (settings.sentry_dsn or "").strip()
    except Exception:
        return ""
    if raw.startswith(("http://", "https://")) and "@" in raw:
        return raw
    return ""


def privacy_options() -> dict[str, Any]:
    """No user data attached by the framework, in the spelling the installed SDK accepts.

    Newer sentry-sdk deprecates `send_default_pii` for the top-level `data_collection`
    (it warns on every Client, even with False); older ones only know `send_default_pii`.

        privacy_options()   ->  {"send_default_pii": False}                   # older SDK
        privacy_options()   ->  {"data_collection": {"user_info": False,
                                 "database_query_data": False, "queues": False, ...}}

    Whether the SDK takes `data_collection` is read off `ClientConstructor`'s signature.
    """
    try:
        from sentry_sdk.consts import (
            ClientConstructor,  # pyright: ignore[reportMissingImports]
        )

        accepts_data_collection = (
            "data_collection" in inspect.signature(ClientConstructor.__init__).parameters
        )
    except Exception:
        accepts_data_collection = False

    if not accepts_data_collection:
        return {"send_default_pii": False}
    return {
        "data_collection": {
            "user_info": False,
            "database_query_data": False,
            "queues": False,
            "graphql": {"document": False, "variables": False},
            "gen_ai": {"inputs": False, "outputs": False},
        }
    }


def client_for(release: str) -> Optional[Any]:
    """The framework's own client for a release. Never touches the host's client.

        client_for("sincpro-odoo:18.5.0")
            ->  sentry_sdk.Client(release="sincpro-odoo:18.5.0", environment="acme", ...)
        client_for("sincpro-odoo:18.5.0")   ->  the same client, from the registry
        client_for("")                      ->  None

    1. No sentry-sdk, no release or no usable DSN (`dsn`): `None`.
    2. A client already built for this release (`registry.error_client`) is the answer.
    3. A new one: the DSN, the release, `traces_sample_rate=0.0`, no auto-enabled
       integrations and the privacy options (`privacy_options`).
       3.1 the deployment's tenant, when there is one, as its `environment`.
    Final: the client is registered for the release and returned.
    """
    if not SDK_AVAILABLE or not release:
        return None
    configured_dsn = dsn()
    if not configured_dsn:
        return None
    cached = registry.error_client(release)
    if cached is not None:
        return cached

    import sentry_sdk  # pyright: ignore[reportMissingImports]

    client_kwargs: dict[str, Any] = {
        "dsn": configured_dsn,
        "release": release,
        "traces_sample_rate": 0.0,
        "auto_enabling_integrations": False,
        **privacy_options(),
    }
    environment = tenant()
    if environment:
        client_kwargs["environment"] = environment
    client = sentry_sdk.Client(**client_kwargs)
    registry.register_error_client(release, client)
    return client


def setup_errors(identity: ObservabilityIdentity) -> ComponentStatus:
    """Prepare the isolated client for this bus's release.

        setup_errors(identity)   ->  on("init")
        setup_errors(identity)   ->  off("dsn_missing")          # no usable DSN configured
        setup_errors(identity)   ->  off("sdk_missing")          # sentry-sdk not installed

    `failed("client_not_created")` when no client came out of it, and the exception's own
    status (`failure_of`) when building one raised.
    """
    try:
        if not SDK_AVAILABLE:
            return off("sdk_missing")
        if not dsn():
            return off("dsn_missing")
        if client_for(identity.release) is None:
            return failed("client_not_created")
        return on("init")
    except Exception as exc:
        return failure_of(exc)
