"""Every tenant — the use case of running the same work once per tenant, each in its own tenant
context: a cron's work for every customer (spatie's `eachCurrent`).

    each(resolution, run, ["acme", "globex"])   ->  {"acme": …, "globex": …}

A tenant that fails never stops the others; every failure is raised together at the end.
"""

from collections.abc import Callable, Iterable
from typing import Any

from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.tenancy.domain.exceptions import TenantRequired
from sincpro_framework.tenancy.services.resolution import Resolution
from sincpro_framework.tenancy.services.switching import working_in


def tenants_to_run(resolution: Resolution, tenants: Iterable[str] | None) -> list[str]:
    """The tenants named, or every active one of the catalog."""
    if tenants is not None:
        return list(tenants)
    if resolution.catalog is None:
        raise TenantRequired(
            "each tenant needs a catalog to list them — Tenancy(catalog=...)"
        )
    return resolution.catalog.ids()


def each(
    resolution: Resolution, run: Callable[[], Any], tenants: Iterable[str] | None = None
) -> dict[str, Any]:
    """`run` once per tenant, each in its own tenant context; every failure raised at the end as
    an `ExceptionGroup`, each noted with its tenant."""
    answers: dict[str, Any] = {}
    failures: list[Exception] = []
    for one in tenants_to_run(resolution, tenants):
        try:
            with working_in(resolution, [one], one):
                answers[one] = run()
        except ProgrammingError:
            raise  # the code's mistake is the same in every tenant: stop at the first
        except Exception as failure:
            logger.exception("a tenant's run failed", tenant=one)
            failure.add_note(f"tenant {one!r}")
            failures.append(failure)
    if failures:
        raise ExceptionGroup(f"{len(failures)} tenant(s) failed", failures)
    return answers
