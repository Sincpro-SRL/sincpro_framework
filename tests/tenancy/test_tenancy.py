"""Every case of tenancy, one test each, through the doors a project uses: a decorator on a use
case, a `with` in a handler, `self.tenancy`. Each door reaches the same use case of
`tenancy/services`, so the same case answers the same way whichever door it came by.

The store knows nothing of tenants: `MemoryRepository()` with no filter. What is partitioned is
declared — `Invoice` by the `TenantOwnedMixin` recipe, `Order` and `Branch` by
`tenancy.discriminator` — and `Note`, with a `tenant_id` nobody declared, is a field like any
other.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import EntrypointKind
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.exceptions import AggregateNotFound
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.tenancy import (
    GetTenantContext,
    InMemoryMemberships,
    InMemoryTenantCatalog,
    JoinTenant,
    ListTenantMembers,
    ListTenants,
    PutTenant,
    ResponseTenantContext,
    ResponseTenantMembers,
    ResponseTenants,
    Shared,
    Tenancy,
    Tenant,
    TenantNotAllowed,
    TenantOwnedMixin,
    TenantRequired,
    TenantStatus,
    TenantUseCases,
    WhenMissing,
)


@dataclass
class Invoice(TenantOwnedMixin, Entity):
    total: Decimal = Decimal("0")


@dataclass
class Order(Entity):
    tenant_id: str | None = None
    code: str = ""


@dataclass
class Branch(Entity):
    company_id: str | None = None
    name: str = ""


@dataclass
class Product(Entity):
    tenant_id: str | None = None
    name: str = ""


@dataclass
class Note(Entity):
    tenant_id: str | None = None
    text: str = ""


class ResponseCount(DataTransferObject):
    value: int


class CommandIssue(DataTransferObject):
    total: Decimal


class CommandIssueAnywhere(DataTransferObject):
    total: Decimal


class CommandCount(DataTransferObject):
    pass


class CommandCountInBlock(DataTransferObject):
    pass


class CommandEveryone(DataTransferObject):
    pass


class CommandWhere(DataTransferObject):
    pass


class CommandOptionalStep(DataTransferObject):
    pass


class CommandCatalog(DataTransferObject):
    pass


class CommandVoid(DataTransferObject):
    id: str


class CommandRemind(DataTransferObject):
    pass


reminded: list[str | None] = []

CALLER = EntrypointKind.REST
"""What a caller sends through a wire is judged; what code writes in `bus.context` is the code's."""


def billing(
    tenancy: Tenancy, store: MemoryRepository, isolated: bool = False
) -> UseFramework:
    bus = UseFramework("tenancy-billing", log_after_execution=False)
    bus.add_dependency("store", store)
    tenancy.on(bus, isolated=isolated)

    @bus.feature(CommandIssue)
    @tenancy.isolated
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> ResponseCount:
            self.store.save(Invoice(total=dto.total))
            return ResponseCount(value=1)

    @bus.feature(CommandIssueAnywhere)
    class IssueAnywhere(Feature):
        def execute(self, dto: CommandIssueAnywhere) -> ResponseCount:
            self.store.save(Invoice(total=dto.total))
            return ResponseCount(value=1)

    @bus.feature(CommandCount)
    @tenancy.isolated
    class Count(Feature):
        def execute(self, dto: CommandCount) -> ResponseCount:
            return ResponseCount(value=self.store.count(Invoice, Criteria()).value)

    @bus.feature(CommandCountInBlock)
    class CountInBlock(Feature):
        def execute(self, dto: CommandCountInBlock) -> ResponseCount:
            with self.tenancy.isolated():
                return ResponseCount(value=self.store.count(Invoice, Criteria()).value)

    @bus.app_service(CommandEveryone)
    @tenancy.without_tenant(reason="consolidated report")
    class Everyone(ApplicationService):
        def execute(self, dto: CommandEveryone) -> ResponseCount:
            return self.feature_bus.execute(CommandCount(), ResponseCount)

    @bus.feature(CommandWhere)
    @tenancy.required
    class Where(Feature):
        def execute(self, dto: CommandWhere) -> ResponseTenantContext:
            return ResponseTenantContext(active=self.tenancy.active)

    @bus.feature(CommandOptionalStep)
    @tenancy.isolated(when_missing=WhenMissing.SKIP)
    class OptionalStep(Feature):
        def execute(self, dto: CommandOptionalStep) -> ResponseCount:
            return ResponseCount(value=self.store.count(Invoice, Criteria()).value)

    @bus.feature(CommandCatalog)
    @tenancy.isolated(when_missing=WhenMissing.SHARED)
    class Catalog(Feature):
        def execute(self, dto: CommandCatalog) -> ResponseCount:
            return ResponseCount(value=self.store.count(Product, Criteria()).value)

    @bus.feature(CommandVoid)
    class Void(Feature):
        def execute(self, dto: CommandVoid) -> ResponseCount:
            invoices = self.tenancy.repository(self.store)
            invoice = invoices.get(Invoice, dto.id)
            if invoice is None:
                raise AggregateNotFound(dto.id)
            invoices.save(invoice)
            self.store.save(Note(text=f"voided {dto.id}"))  # this repository is not isolated
            return ResponseCount(value=1)

    @bus.app_service(CommandRemind)
    @tenancy.each_tenant
    @tenancy.isolated
    class Remind(ApplicationService):
        def execute(self, dto: CommandRemind) -> None:
            reminded.append(self.tenancy.active)

    return bus


@pytest.fixture
def store() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def tenancy() -> Tenancy:
    tenancy = Tenancy(catalog=InMemoryTenantCatalog.of("acme", "globex"))
    tenancy.discriminator(Order)
    tenancy.discriminator(Branch, field="company_id")
    tenancy.discriminator(Product, shared=Shared.WHEN_EMPTY)
    return tenancy


@pytest.fixture
def bus(tenancy, store) -> UseFramework:
    return billing(tenancy, store)


def _issue(bus: UseFramework, tenant: str, times: int = 1) -> None:
    with bus.context({"tenant_id": tenant}):
        for _ in range(times):
            bus(CommandIssue(total=Decimal(1)))


def _count(bus: UseFramework, context: dict, dto=None) -> int | None:
    with bus.context(context):
        answered = bus(dto or CommandCount(), ResponseCount)
    return None if answered is None else answered.value


# 1 — a use case of one tenant ------------------------------------------------------------
def test_1_an_isolated_use_case_writes_stamped_and_reads_only_its_tenant(bus, store):
    _issue(bus, "acme", 2)
    _issue(bus, "globex")
    assert _count(bus, {"tenant_id": "acme"}) == 2
    assert {one.tenant_id for one in store.fetch_all(Invoice).items} == {"acme", "globex"}
    with pytest.raises(TenantRequired):
        _count(bus, {})


# 2 — a public use case, no tenant -----------------------------------------------------------
def test_2_a_use_case_that_declares_nothing_is_never_touched(bus, store):
    with bus.context({}):
        bus(CommandIssueAnywhere(total=Decimal(5)))
    assert store.fetch_all(Invoice).items[0].tenant_id is None


# 3 — the code names the tenant, from the data -----------------------------------------------
def test_3_the_code_works_in_the_tenant_its_data_names(tenancy, store):
    invitation_tenant = "globex"
    with tenancy.with_tenant(invitation_tenant), tenancy.isolated():
        store.save(Invoice(total=Decimal(1)))
    assert store.fetch_all(Invoice).items[0].tenant_id == "globex"


# 4 — an optional step that must not fail -----------------------------------------------------
def test_4_skip_does_not_run_without_a_tenant_and_answers_none(bus):
    assert _count(bus, {}, CommandOptionalStep()) is None
    _issue(bus, "acme")
    assert _count(bus, {"tenant_id": "acme"}, CommandOptionalStep()) == 1


def test_4_a_block_cannot_skip_its_own_body(tenancy):
    with pytest.raises(ProgrammingError, match="skips a whole use case"):
        with tenancy.isolated(when_missing=WhenMissing.SKIP):
            pass


# 5 — a catalog of shared and own records ---------------------------------------------------
def test_5_shared_reads_only_the_shared_without_a_tenant(bus, tenancy, store):
    store.add(Product(name="everyone's"))
    with tenancy.with_tenant("acme"), tenancy.isolated():
        store.save(Product(name="acme's"))
    assert _count(bus, {}, CommandCatalog()) == 1
    assert _count(bus, {"tenant_id": "acme"}, CommandCatalog()) == 2
    assert _count(bus, {"tenant_id": "globex"}, CommandCatalog()) == 1


# 6 — what is not mine is not found ---------------------------------------------------------
def test_6_writing_a_record_of_another_tenant_is_not_found(tenancy, store):
    theirs = Invoice(total=Decimal(1), tenant_id="globex")
    store.add(theirs)
    with tenancy.with_tenant("acme"), tenancy.isolated():
        with pytest.raises(AggregateNotFound):
            store.save(theirs)
        assert store.get(Invoice, theirs.id) is None


# 8 — a tenant required, nothing filtered -----------------------------------------------------
def test_8_required_refuses_without_a_tenant_and_filters_nothing(bus):
    with pytest.raises(TenantRequired):
        with bus.context({}):
            bus(CommandWhere())
    with bus.context({"tenant_id": "acme"}):
        assert bus(CommandWhere(), ResponseTenantContext).active == "acme"


# 9 — a block ---------------------------------------------------------------------------------
def test_9_a_block_isolates_what_it_runs(bus):
    _issue(bus, "acme")
    _issue(bus, "globex")
    assert _count(bus, {"tenant_id": "acme"}, CommandCountInBlock()) == 1


# 10 — one repository isolated, another not -------------------------------------------------
def test_10_a_repository_view_isolates_its_calls_only(bus, store):
    theirs = Invoice(total=Decimal(1), tenant_id="globex")
    mine = Invoice(total=Decimal(1), tenant_id="acme")
    store.add(theirs, mine)
    with bus.context({"tenant_id": "acme"}):
        bus(CommandVoid(id=mine.id))
        with pytest.raises(AggregateNotFound):
            bus(CommandVoid(id=theirs.id))
    assert [one.tenant_id for one in store.fetch_all(Note).items] == [None]


# 11 — the condition written out -------------------------------------------------------------
def test_11_criteria_is_the_isolation_written_out(tenancy):
    asked = Criteria(selection=Condition(field="total", operator=Operator.GT, value=1))
    with tenancy.with_tenants(["acme", "globex"]):
        written = tenancy.criteria(Invoice, asked)
    dumped = written.model_dump(mode="json")["selection"]
    assert {"field": "tenant_id", "operator": "in", "value": ["acme", "globex"]} in dumped[
        "all"
    ]
    assert tenancy.criteria(Note, asked) is asked  # not partitioned: as given


# 12 — across tenants, by name ---------------------------------------------------------------
def test_12_without_tenant_reads_every_tenant_and_the_nearest_declaration_wins(bus):
    _issue(bus, "acme")
    _issue(bus, "globex")
    # Everyone crosses; the Count it calls is isolated and wins inside it: it needs a tenant.
    with pytest.raises(TenantRequired):
        _count(bus, {}, CommandEveryone())
    with bus.context({"tenant_ids": ["acme", "globex"]}):
        assert bus(CommandEveryone(), ResponseCount).value == 2


# 13 — every tenant --------------------------------------------------------------------------
def test_13_each_tenant_runs_once_per_active_tenant(bus, tenancy):
    reminded.clear()
    with bus.context({}):
        bus(CommandRemind())
    assert reminded == ["acme", "globex"]
    assert tenancy.each(lambda: tenancy.active) == {"acme": "acme", "globex": "globex"}


# 14 — a platform that works in any tenant --------------------------------------------------
def test_14_cross_tenant_lets_the_platform_name_any_tenant():
    support = {"on": False}
    tenancy = Tenancy(
        memberships=InMemoryMemberships({"ana": ["acme"]}), cross_tenant=lambda: support["on"]
    )
    bus = billing(tenancy, MemoryRepository())
    with bus.context({"user_id": "ana", "tenant_id": "globex"}, kind=CALLER):
        with pytest.raises(TenantNotAllowed):
            bus(CommandWhere())
        assert tenancy.allows("globex") is False
        support["on"] = True
        assert bus(CommandWhere(), ResponseTenantContext).active == "globex"
        assert tenancy.allows("globex") is True


# 15 — a member of several ------------------------------------------------------------------
def test_15_memberships_validate_what_the_caller_names(store):
    tenancy = Tenancy(
        memberships=InMemoryMemberships({"ana": ["acme"], "bob": ["acme", "globex"]})
    )
    bus = billing(tenancy, store)
    with bus.context({"user_id": "ana", "tenant_id": "globex"}, kind=CALLER):
        with pytest.raises(TenantNotAllowed):
            bus(CommandWhere())
    with bus.context({"user_id": "ana"}, kind=CALLER):
        assert bus(CommandWhere(), ResponseTenantContext).active == "acme"
    with bus.context({"user_id": "bob"}, kind=CALLER):
        with pytest.raises(TenantRequired, match="belong to several"):
            bus(CommandWhere())
    with bus.context({"user_id": "bob", "tenant_ids": ["acme", "globex"]}, kind=CALLER):
        assert bus(CommandCount(), ResponseCount).value == 0


def test_15_a_tenant_the_catalog_holds_suspended_is_never_served(store):
    catalog = InMemoryTenantCatalog(
        Tenant("acme"), Tenant("globex", status=TenantStatus.SUSPENDED)
    )
    bus = billing(Tenancy(catalog=catalog), store)
    with bus.context({"tenant_id": "globex"}):
        with pytest.raises(TenantNotAllowed, match="suspended"):
            bus(CommandWhere())


# 16 — a bus isolated by default -------------------------------------------------------------
def test_16_a_bus_isolated_by_default_isolates_what_nobody_wrapped(tenancy, store):
    bus = billing(tenancy, store, isolated=True)
    with pytest.raises(TenantRequired):
        with bus.context({}):
            bus(CommandIssueAnywhere(total=Decimal(1)))
    with bus.context({"tenant_id": "acme"}):
        bus(CommandIssueAnywhere(total=Decimal(1)))
    assert store.fetch_all(Invoice).items[0].tenant_id == "acme"


# 17 — what nobody declared is a field like any other ---------------------------------------
def test_17_a_tenant_id_nobody_declared_is_never_filtered(tenancy, store):
    store.add(Note(text="x", tenant_id="globex"))
    with tenancy.with_tenant("acme"), tenancy.isolated():
        assert store.count(Note, Criteria()).value == 1


def test_another_field_and_a_shared_catalog_are_declared(tenancy, store):
    @dataclass
    class Currency(Entity):
        tenant_id: str | None = None
        code: str = ""

    tenancy.discriminator(Currency)
    tenancy.shared(Currency)
    with tenancy.with_tenant("acme"), tenancy.isolated():
        store.save(Branch(name="centro"))
        store.save(Currency(code="BOB"))
    with tenancy.with_tenant("globex"), tenancy.isolated():
        assert store.count(Branch, Criteria()).value == 0
        assert store.count(Currency, Criteria()).value == 1
    assert store.fetch_all(Branch).items[0].company_id == "acme"


# the built-in use cases ----------------------------------------------------------------------
class QueryMe(GetTenantContext):
    pass


class CommandAddMember(JoinTenant):
    pass


class QueryMembers(ListTenantMembers):
    pass


class CommandSuspend(PutTenant):
    pass


class QueryActive(ListTenants):
    pass


def test_the_built_in_use_cases_answer_the_tenant_context_catalog_and_memberships():
    tenancy = Tenancy(
        memberships=InMemoryMemberships({"ana": ["acme"]}),
        catalog=InMemoryTenantCatalog.of("acme", "globex"),
    )
    bus = UseFramework("tenancy-admin", log_after_execution=False)
    tenancy.on(bus)

    @bus.feature([QueryMe, CommandAddMember, QueryMembers, CommandSuspend, QueryActive])
    class Tenants(TenantUseCases):
        pass

    bus(CommandAddMember(subject="ana", tenant="globex", role="admin"))
    with bus.context({"user_id": "ana", "tenant_id": "globex"}, kind=CALLER):
        me = bus(QueryMe(), ResponseTenantContext)
    assert me is not None
    assert (me.active, me.allowed) == ("globex", ["acme", "globex"])
    assert me.tenant is not None and me.tenant.id == "globex"
    members = bus(QueryMembers(tenant="globex"), ResponseTenantMembers)
    assert members is not None and members.members == {"ana": "admin"}
    bus(CommandSuspend(id="globex", status=TenantStatus.SUSPENDED))
    active = bus(QueryActive(), ResponseTenants)
    assert active is not None and [one.id for one in active.tenants] == ["acme"]


# a cached Query answers each tenant its own ---------------------------------------------------
class QueryTotal(DataTransferObject):
    pass


def test_a_cached_query_answers_each_tenant_its_own(tenancy, store):
    bus = billing(tenancy, store)

    @bus.feature(QueryTotal)
    @tenancy.isolated
    class Total(Feature):
        def execute(self, dto: QueryTotal) -> ResponseCount:
            return ResponseCount(value=self.store.count(Invoice, Criteria()).value)

    bus.cache.queries(QueryTotal)
    _issue(bus, "acme")
    assert _count(bus, {"tenant_id": "acme"}, QueryTotal()) == 1
    assert _count(bus, {"tenant_id": "globex"}, QueryTotal()) == 0


def test_a_shared_record_edited_inside_a_tenant_stays_everyone_s(tenancy, store):
    """Only a new record takes the active tenant: an edit of a shared one never moves it."""
    shared = Product(name="everyone's")
    store.add(shared)
    with tenancy.with_tenant("acme"), tenancy.isolated():
        found = store.get(Product, shared.id)
        assert found is not None
        found.name = "renamed"
        store.save(found)
    assert store.get(Product, shared.id).tenant_id is None  # type: ignore[union-attr]


class CountingMemberships(InMemoryMemberships):
    asked = 0

    def tenants_of(self, subject):
        CountingMemberships.asked += 1
        return super().tenants_of(subject)


def test_memberships_are_asked_once_per_execution_and_never_cached_across(store):
    """A nested use case in the same tenants is not validated again; the next request is —
    a member taken out of a tenant is refused from then on."""
    memberships = CountingMemberships({"ana": ["acme", "globex"]})
    bus = billing(Tenancy(memberships=memberships), store)
    CountingMemberships.asked = 0
    with bus.context({"user_id": "ana", "tenant_ids": ["acme", "globex"]}, kind=CALLER):
        bus(CommandEveryone(), ResponseCount)  # Everyone → Count, nested
    assert CountingMemberships.asked == 1

    memberships.leave("ana", "globex")
    with bus.context({"user_id": "ana", "tenant_id": "globex"}, kind=CALLER):
        with pytest.raises(TenantNotAllowed):
            bus(CommandWhere())


def test_an_anonymous_caller_names_no_tenant_and_the_code_names_any(store):
    tenancy = Tenancy(memberships=InMemoryMemberships({"ana": ["acme"]}))
    bus = billing(tenancy, store)
    with bus.context({"tenant_id": "acme"}, kind=CALLER):  # nobody: no membership anywhere
        with pytest.raises(TenantNotAllowed):
            bus(CommandWhere())
    with tenancy.with_tenant("globex"):  # the code's decision: an invitation, a link signed
        assert bus(CommandWhere(), ResponseTenantContext).active == "globex"


class CommandAnswersByMistake(DataTransferObject):
    pass


def test_each_tenant_stops_at_the_first_tenant_when_the_use_case_answers(tenancy, store):
    ran: list[str | None] = []
    bus = billing(tenancy, store)

    @bus.app_service(CommandAnswersByMistake)
    @tenancy.each_tenant
    class AnswersByMistake(ApplicationService):
        def execute(self, dto: CommandAnswersByMistake) -> ResponseCount:
            ran.append(self.tenancy.active)
            return ResponseCount(value=1)

    with pytest.raises(ProgrammingError, match="answers nothing"):
        bus(CommandAnswersByMistake())
    assert ran == ["acme"]


def test_an_upsert_in_memory_never_takes_a_key_another_tenant_holds(tenancy, store):
    store.add(Product(name="gold", tenant_id="globex"))
    with tenancy.with_tenant("acme"), tenancy.isolated():
        done = store.upsert([Product(name="gold")], on=["name"])
    assert (done.written, done.skipped) == (0, 1)
    assert [one.tenant_id for one in store.fetch_all(Product).items] == ["globex"]
