"""The reads of one aggregate, written once: `Get`, `GetMany`, `LiteralSearch` and `Search`,
answered by one Feature that reads what the entity's `presentation` declared.

    class ResponseAccount(ResponseRecord):
        account: Account

    class ResponseAccounts(ResponseRecords):
        accounts: list[Account]

    class ResponseListAccounts(ResponsePaginatedQuery):
        accounts: list[Account]

    class QueryGetAccount(Get[Account, ResponseAccount]):                   one record, its detail
        pass

    class QueryGetManyAccounts(GetMany[Account, ResponseAccounts]):         several, by identity
        pass

    class QueryFindAccounts(LiteralSearch[Account, ResponseListAccounts]):  a select's literal
        pass

    class QueryListAccounts(Search[Account, ResponseListAccounts]):         a page by criteria
        pass

    @accounting.feature(
        [QueryGetAccount, QueryGetManyAccounts, QueryFindAccounts, QueryListAccounts]
    )
    class AccountReads(EntityReads[Account]):
        pass

Context: the DTO names the entity and the response in its base, so one Feature answers any of
them and a project names its own DTOs. Each read is a method (`get`, `get_many`,
`literal_search`, `search`): override one and call `super()` to act before or after it.
`reads_from` is the repository, the dependency named `repository` unless overridden. Nothing here is required: a
Feature written by hand with `matching`, `detail_of` and `repository.search` does the same.
"""

from typing import Any

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Expression,
    Operator,
    Pagination,
)
from sincpro_framework.ddd.entity.entity_collection import identity_name
from sincpro_framework.ddd.entity.query_entity import detail_of, matching
from sincpro_framework.ddd.exceptions import AggregateNotFound, ContractViolation
from sincpro_framework.ddd.query import (
    Query,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
)
from sincpro_framework.ddd.repositories.repository import IRepository
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature


class Get[T, R: ResponseRecord](DataTransferObject):
    """One record by its identity, with the relations the entity's `detail` names.

    QueryGetAccount(id="01a1…")                                    the declared detail
    QueryGetAccount(id="01a1…", criteria=Criteria(specification=…)) narrowed by the caller
    """

    id: str
    criteria: Criteria = Criteria()
    """Merged over the declared detail: its specification can only narrow it."""


class GetMany[T, R: ResponseRecords](DataTransferObject):
    """Several records by their identities, as a list in the order asked, each with the
    declared detail. Not a page: every identity asked is answered, or listed as missing.

    QueryGetManyAccounts(ids=["01a1…", "01a2…"])
    """

    ids: list[str]
    criteria: Criteria = Criteria()
    """Merged over the declared detail: its specification can only narrow it."""


class LiteralSearch[T, R: ResponsePaginatedQuery](DataTransferObject):
    """What a select asks: the literal the user typed, matched the way the entity's `search`
    says. A short page of identity and display; a blank literal is the first page in order."""

    text: str = ""
    criteria: Criteria = Criteria()
    """Merged over the match: a filter the select adds, a page size it prefers."""


class Search[T, R: ResponsePaginatedQuery](Query):
    """A page by criteria, ordered as the entity declared when the criteria names no order."""


def declared_by(dto: type) -> tuple[type, type]:
    """The entity and the response a DTO named in its base.

    in      QueryGetAccount        →  out  (Account, ResponseAccount)
    """
    for base in dto.__mro__:
        generic = getattr(base, "__pydantic_generic_metadata__", None)
        arguments = generic.get("args", ()) if generic else ()
        if len(arguments) == 2 and all(isinstance(one, type) for one in arguments):
            return arguments[0], arguments[1]
    raise ContractViolation(
        f"{dto.__name__} does not name its entity and response: "
        f"class {dto.__name__}(Get[Account, ResponseAccount])"
    )


class EntityReads[T](Feature):
    """The Feature that answers `Get`, `LiteralSearch` and `Search` for one entity.

    @accounting.feature([QueryGetAccount, QueryFindAccounts, QueryListAccounts])
    class AccountReads(EntityReads[Account]):
        def get(self, dto: QueryGetAccount) -> ResponseAccount:
            answer = super().get(dto)        # after: the record is in hand
            ...
            return answer
    """

    def reads_from(self) -> IRepository:
        """The repository read from: the dependency named `repository`. Override it when the
        bounded context named it otherwise."""
        repository = getattr(self, "repository", None)
        if not isinstance(repository, IRepository):
            raise ContractViolation(
                f"{type(self).__name__} reads from the dependency named 'repository', and the "
                "bus has none: add it, or override reads_from()"
            )
        return repository

    def execute(self, dto: Any) -> Any:
        match dto:
            case Get():
                return self.get(dto)
            case GetMany():
                return self.get_many(dto)
            case LiteralSearch():
                return self.literal_search(dto)
            case Search():
                return self.search(dto)
        raise ContractViolation(
            f"{type(self).__name__} answers Get, GetMany, LiteralSearch and Search, not "
            f"{type(dto).__name__}"
        )

    def get(self, dto: Any) -> Any:
        """The record and its declared detail, or `AggregateNotFound`."""
        entity, response = declared_by(type(dto))
        criteria = self.by_identity(
            entity, Condition(field=identity_name(entity), value=dto.id), 1, dto.criteria
        )
        page = self.reads_from().search(entity, criteria)
        if not page.items:
            raise AggregateNotFound(f"{entity.__name__} {dto.id} does not exist")
        return response.of(page.items[0], page, criteria)

    def get_many(self, dto: Any) -> Any:
        """The records of `ids`, in that order, each with its declared detail; the ids with no
        record are listed as missing."""
        entity, response = declared_by(type(dto))
        ids = list(dict.fromkeys(dto.ids))
        identity = identity_name(entity)
        criteria = self.by_identity(
            entity,
            Condition(field=identity, value=ids, operator=Operator.IN),
            len(ids),
            dto.criteria,
        )
        page = self.reads_from().search(entity, criteria)
        return response.of(ids, page, criteria, identity)

    def by_identity(
        self, entity: type, where: Expression, limit: int, asked: Criteria
    ) -> Criteria:
        """The declared detail narrowed by `asked`, filtered by identity, read as one page.

        A page and not `repository.get`/`browse`, so the definition and the relations come
        back the same way from every store and the answer is cut by the same specification.
        """
        declared = detail_of(entity) or Criteria()
        return declared.merged_with(asked).model_copy(
            update={
                "where": where,
                "pagination": Pagination(limit=limit),
                "count": CountMode.NONE,
            }
        )

    def literal_search(self, dto: Any) -> Any:
        """The short page a select shows for the literal."""
        entity, response = declared_by(type(dto))
        criteria = matching(entity, dto.text).merged_with(dto.criteria)
        return response.of(self.reads_from().search(entity, criteria), criteria)

    def search(self, dto: Any) -> Any:
        """The page the criteria asks for."""
        entity, response = declared_by(type(dto))
        return response.of(self.reads_from().search(entity, dto.criteria), dto.criteria)
