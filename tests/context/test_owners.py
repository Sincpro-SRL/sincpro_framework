"""Whose a draft or a pipeline run is — read from the context, always in one shape, for every shape a
system has, refusing none.

    tenant_id=…,tenant_ids=…,<the keys of by>=…

No tenant and no user, a tenant, a user, a user in a tenant, a user across several tenants: each is
a way a system is, and none is forced on a project. A key the context does not say — missing,
`None`, empty — says nothing, and its value is left empty. Values are encoded, so no value is ever
read as a separator."""

from datetime import UTC, datetime

import pytest

from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.context import (
    ContextStore,
    InMemoryContexts,
    KeyValueContexts,
    use_context,
)
from sincpro_framework.context.entrypoint.owner import owner_of
from sincpro_framework.pipelines import PipelineRun

SHAPES = [
    pytest.param({}, "tenant_id=,tenant_ids=,user_id=", id="no tenant, no user"),
    pytest.param(
        {"tenant_id": None, "tenant_ids": None, "user_id": None},
        "tenant_id=,tenant_ids=,user_id=",
        id="all said as None",
    ),
    pytest.param(
        {"tenant_id": "", "tenant_ids": [], "user_id": ""},
        "tenant_id=,tenant_ids=,user_id=",
        id="all said empty",
    ),
    pytest.param({"tenant_id": "acme"}, "tenant_id=acme,tenant_ids=,user_id=", id="a tenant"),
    pytest.param({"user_id": "ana"}, "tenant_id=,tenant_ids=,user_id=ana", id="a user"),
    pytest.param(
        {"tenant_id": "acme", "user_id": "ana"},
        "tenant_id=acme,tenant_ids=,user_id=ana",
        id="a user in a tenant",
    ),
    pytest.param(
        {"tenant_ids": ["globex", "acme"], "user_id": "ana"},
        "tenant_id=,tenant_ids=acme|globex,user_id=ana",
        id="a user across tenants",
    ),
    pytest.param(
        {"tenant_id": "acme", "tenant_ids": ["acme", "globex"], "user_id": "ana"},
        "tenant_id=acme,tenant_ids=acme|globex,user_id=ana",
        id="a user acting in one of its tenants",
    ),
]

STORES = [
    pytest.param(InMemoryContexts, id="memory"),
    pytest.param(lambda: KeyValueContexts(InMemoryKeyValue()), id="key-value"),
]


@pytest.mark.parametrize(("said", "owner"), SHAPES)
def test_the_owner_has_one_shape_whatever_the_context_says(said, owner):
    assert owner_of(said, ["user_id"]) == owner


@pytest.mark.parametrize("make", STORES)
@pytest.mark.parametrize(("said", "owner"), SHAPES)
def test_a_draft_is_kept_and_read_back_for_every_shape_and_refused_for_none(
    make, said, owner
):
    store = make()
    with use_context().scoped(said) as context:
        drafts = context.drafts(store=store)
        kept = drafts.keep("billing.Invoice:01a2", {"discount": "10"}, 3, 0)
        read = drafts.read("billing.Invoice:01a2")
        listed = drafts.subjects()

    assert drafts.owner == owner
    assert read == kept
    assert listed == ["billing.Invoice:01a2"]


@pytest.mark.parametrize("make", STORES)
@pytest.mark.parametrize(("said", "owner"), SHAPES)
def test_a_pipeline_run_is_kept_and_found_for_every_shape_and_refused_for_none(
    make, said, owner
):
    store = make()
    with use_context().scoped(said) as context:
        runs = context.pipelines(by=["user_id"], store=store)
        runs.keep(
            PipelineRun(
                pipeline="billing.CommandRun", run_id="r-1", started_at=datetime.now(UTC)
            )
        )
        read = runs.read("billing.CommandRun", "r-1")
        found = runs.find("billing.CommandRun")

    assert read is not None and read.owner == owner
    assert [one.run_id for one in found] == ["r-1"]


def keep_and_read(store: ContextStore, writer: dict, reader: dict) -> bool:
    """Whether what `writer`'s context keeps is what `reader`'s context reads."""
    with use_context().scoped(writer) as context:
        context.drafts(store=store).keep("cart", {"items": 1}, 0, 0)
    with use_context().scoped(reader) as context:
        return context.drafts(store=store).read("cart") is not None


@pytest.mark.parametrize("make", STORES)
def test_the_desktop_the_browser_and_the_phone_of_one_user_share_one_draft(make):
    """No execution, no session: the same user in the same tenant, from three devices."""
    store = make()
    ana = {"tenant_id": "acme", "user_id": "ana"}

    with use_context().scoped({**ana, "device": "desktop"}) as desktop:
        drafts = desktop.drafts(store=store)
        first = drafts.keep("billing.Invoice:01a2", {"discount": "10"}, 3, 0)
    with use_context().scoped({**ana, "device": "browser"}) as browser:
        read = browser.drafts(store=store).read("billing.Invoice:01a2")
    with use_context().scoped({**ana, "device": "phone"}) as phone:
        phone.drafts(store=store).keep(
            "billing.Invoice:01a2", {"discount": "20"}, 3, first.version
        )

    assert read == first


@pytest.mark.parametrize("make", STORES)
def test_nothing_is_shared_across_tenants_nor_across_users_of_one_tenant(make):
    store = make()
    ana = {"tenant_id": "acme", "user_id": "ana"}

    assert keep_and_read(store, ana, ana)
    assert not keep_and_read(make(), ana, {"tenant_id": "acme", "user_id": "bob"})
    assert not keep_and_read(make(), ana, {"tenant_id": "globex", "user_id": "ana"})
    assert not keep_and_read(make(), ana, {"tenant_id": "acme"})


@pytest.mark.parametrize("make", STORES)
def test_a_service_with_no_tenant_and_no_user_keys_its_drafts_by_its_own_ids(make):
    assert keep_and_read(make(), {}, {"tenant_id": None, "user_id": None})


@pytest.mark.parametrize("make", STORES)
def test_the_order_of_the_tenants_a_user_acts_across_is_one_owner(make):
    assert keep_and_read(
        make(),
        {"tenant_ids": ["globex", "acme"], "user_id": "ana"},
        {"tenant_ids": ["acme", "globex"], "user_id": "ana"},
    )


@pytest.mark.parametrize("make", STORES)
@pytest.mark.parametrize(
    ("writer", "reader"),
    [
        ({"tenant_id": "acme/user_id=bob"}, {"tenant_id": "acme", "user_id": "bob"}),
        ({"tenant_id": "acme,user_id=bob"}, {"tenant_id": "acme", "user_id": "bob"}),
        ({"tenant_ids": ["acme|globex"]}, {"tenant_ids": ["acme", "globex"]}),
        ({"tenant_id": "acme:x"}, {"tenant_id": "acme", "user_id": "x"}),
    ],
    ids=["a slash", "a comma", "a bar", "a colon"],
)
def test_a_value_that_spells_a_separator_never_reads_another_owners_draft(
    make, writer, reader
):
    assert not keep_and_read(make(), writer, reader)


@pytest.mark.parametrize("make", STORES)
def test_what_is_meant_to_be_shared_by_the_tenant_is_owned_by_nobody_but_it(make):
    store = make()
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}) as ana:
        ana.drafts(by=[], store=store).keep("settings", {"theme": "dark"}, 0, 0)
    with use_context().scoped({"tenant_id": "acme", "user_id": "bob"}) as bob:
        assert bob.drafts(by=[], store=store).read("settings") is not None
    with use_context().scoped({"tenant_id": "globex", "user_id": "bob"}) as other:
        assert other.drafts(by=[], store=store).read("settings") is None


def test_the_keys_a_project_says_a_draft_belongs_to_come_after_the_tenant_keys():
    assert (
        owner_of({"tenant_id": "acme", "session_id": "s-1"}, ["session_id"])
        == "tenant_id=acme,tenant_ids=,session_id=s-1"
    )
    assert (
        owner_of({"tenant_id": "acme"}, ["tenant_id", "user_id"])
        == "tenant_id=acme,tenant_ids=,user_id="
    )
