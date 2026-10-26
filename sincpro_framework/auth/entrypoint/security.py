"""`security`: who the execution acts as, and the one door every wire goes through to hand auth
what a caller presented — so JSON-RPC, gRPC, MCP, an ASGI app of the project's and a call to
another service all authenticate the same way.

    security.identity                                       # who acts, never None
    with security.as_identity(Identity.user("user:42")):    # what a test opens to be someone
        billing(command)
    with security.as_system("cron: close cash registers"):  # admitted by every requirement
        billing(command)

    credentials = Credentials.from_asgi(scope)              # or Credentials.from_headers(...)
    with security.authenticated_as(bus, credentials):       # the bus's AccessControl, if any
        bus(command)

Context: standard library only — a transport's own library (grpc, fastmcp, starlette) stays in
its entrypoint, so a project that installs none of them imports this all the same. A bus nobody
guards is left alone: no `AccessControl`, no authentication, the identity as it was found.
"""

from collections.abc import Generator, Iterable
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import TYPE_CHECKING

from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.entrypoint.access_control import AccessControl
from sincpro_framework.auth.infrastructure.security_context import (
    as_identity,
    as_system,
    current_identity,
)

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


class SecurityContext:
    """Who the execution in progress acts as, and how a wire makes it act as its caller — one
    object, `security`, so nothing outside auth reaches into how the identity is held."""

    @property
    def identity(self) -> Identity:
        """Who the execution in progress acts as — `Identity.anonymous()` outside any
        `as_identity`, never `None`."""
        return current_identity()

    def as_identity(self, identity: Identity) -> AbstractContextManager[Identity]:
        """Act as `identity` inside the block — what an entrypoint opens once it authenticated
        the caller, and what a test opens to be someone. A use case called inside is decided
        anew; both the identity and the decision are restored on leaving.

            with security.as_identity(Identity.user("user:42", tenant="bo")) as identity:
                security.identity.subject   ->  "user:42"
            security.identity.subject       ->  "anonymous"    # as it was before the block
        """
        return as_identity(identity)

    def as_system(self, reason: str) -> AbstractContextManager[Identity]:
        """Act as the system — a cron, a migration, a consumer — which every requirement
        admits. `reason` is required and logged, the one trace an elevation leaves."""
        return as_system(reason)

    @contextmanager
    def authenticated_as(
        self, bus: "UseFramework", credentials: Credentials | None
    ) -> Generator[Identity, None, None]:
        """Act, inside the block, as whoever `credentials` say is calling `bus` — by the
        `AccessControl` guarding it. No credentials, or a bus nobody guards: the identity in
        play stays. `Unauthenticated` when the credentials are a provider's and fail.

            with security.authenticated_as(billing, Credentials.from_asgi(scope)) as identity:
                identity           ->  Identity("user:42", provider="keycloak")
                billing(command)   # runs as user:42
            with security.authenticated_as(unguarded_bus, credentials) as identity:
                identity           ->  security.identity, unchanged

        Context: the identity is yielded, so the transport can log or answer with it.
        """
        access = AccessControl.of(bus)
        if credentials is None or access is None:
            with nullcontext(current_identity()) as unchanged:
                yield unchanged
            return
        with as_identity(access.authenticate(credentials)) as identity:
            yield identity

    def challenges(self, buses: Iterable["UseFramework"]) -> list[str]:
        """Every `WWW-Authenticate` value the buses' providers answer a 401 with, once each.

            security.challenges([billing, sales])   # both guarded by AccessControl([jwt])
                ->  ['Bearer realm="sincpro"']
            security.challenges([unguarded_bus])    ->  []

        Context: in the order the buses and their providers are given; a repeat is dropped.
        """
        found: list[str] = []
        for bus in buses:
            access = AccessControl.of(bus)
            for challenge in access.challenges() if access else ():
                if challenge not in found:
                    found.append(challenge)
        return found

    def identity_headers(self, context: str) -> dict[str, str]:
        """The headers a call to the service hosting `context` carries, so it knows who the
        call acts for — what the identity's provider issues (`credentials_for`); empty when
        nothing is guarded, nobody is calling, or no provider issues any.

        Context: only what a provider issues travels — never the identity's claims, which may
        hold what must not leave the process (a tenant's own credential).

            # acting as user:42, vouched for by a ServiceTokenProvider
            security.identity_headers("sales")
                ->  {"x-sp-service-token": "eyJhbGciOiJIUzI1NiIs…"}
            # nobody calling, or no AccessControl issuing anything  ->  {}

        The `AccessControl`s guarding a bus of `context` are asked first; the first to issue
        wins.
        """
        identity = current_identity()
        if identity.is_anonymous:
            return {}
        for access in AccessControl.guarding(context):
            issued = access.credentials_for(identity)
            if issued is not None:
                return dict(issued.headers)
        return {}


security = SecurityContext()
"""The one `SecurityContext` of the process — what every wire and every test uses."""
