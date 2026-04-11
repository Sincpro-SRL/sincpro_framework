from enum import StrEnum
from typing import Any, NotRequired, TypedDict, assert_type

from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework


class SIATEnvironment(StrEnum):
    PROD = "prod"
    TEST = "test"


class ContextApp(TypedDict, total=False):
    """Known context keys with strict types for IDE autocompletion."""

    TOKEN: NotRequired[str]
    SIAT_ENV: NotRequired[SIATEnvironment]
    SIGN_KEY_PASSWORD: NotRequired[str]


ContextPayload = dict[str, Any] | ContextApp


class ProxySiatRegistry:
    pass


class DependencyContextType:
    proxy_siat: ProxySiatRegistry
    context: ContextApp


class DependencyContextPayloadType:
    proxy_siat: ProxySiatRegistry
    context: ContextPayload


class Feature(_Feature, DependencyContextType):
    context: ContextApp


class FeatureWithPayload(_Feature, DependencyContextPayloadType):
    context: ContextPayload


class ApplicationService(_ApplicationService, DependencyContextType):
    context: ContextApp


class ApplicationServiceWithPayload(_ApplicationService, DependencyContextPayloadType):
    context: ContextPayload


def verify_feature_context(feature: Feature) -> None:
    token = feature.context.get("TOKEN")
    env = feature.context.get("SIAT_ENV")
    assert_type(token, str | None)
    assert_type(env, SIATEnvironment | None)


def verify_context_union(context: ContextPayload) -> None:
    _ = context


def verify_feature_context_payload(feature: FeatureWithPayload) -> None:
    payload = feature.context
    assert_type(payload, ContextPayload)


def verify_feature_context_direct(feature: Feature) -> None:
    direct = feature.context
    assert_type(direct, ContextApp)


def verify_typed_dict_context_with_manager() -> None:
    siat_soap_sdk = UseFramework("typing-context")

    with siat_soap_sdk.context(
        ContextApp(TOKEN="token", SIAT_ENV=SIATEnvironment.TEST)
    ) as sdk:
        assert_type(sdk, UseFramework)
