"""`ExtensionContract`: what a module that extends an entity it does not own proves in its own CI.

    class TestFiscalExtension(ExtensionContract):
        entity = Invoice                 # the core's entity
        extension = InvoiceFiscal        # the class the module adds
        uses = ("total",)                # the core's fields it reads
        requires = "2.1"                 # the core contract it was written against

        def make_store(self):
            ...                          # a store with the core and the extension mapped

The module runs it against the core it will be installed on, before anyone upgrades: a renamed
field, a contract that moved on, an extension that no longer reads back as itself. pytest collects
the inherited `test_*` methods; nothing here imports pytest.
"""

from typing import Any

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.ddd.criteria import Criteria, Pagination
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.reads.reads import blank


class ExtensionContract:
    """Inherit, name the `entity` and the `extension`, answer `make_store`, and run with pytest."""

    entity: type
    extension: type
    uses: tuple[str, ...] = ()
    """The fields of the core the module reads."""
    requires: str = ""
    """The core contract the module was written against: `"2.1"`, the same major and at least that
    minor. Empty when the core publishes none."""

    def make_store(self) -> Any:
        raise NotImplementedError("answer a store with `entity` and `extension` mapped")

    def _definition(self) -> Any:
        page = self.make_store().search(self.entity, Criteria(pagination=Pagination(limit=1)))
        assert page.meta is not None, "the store gave no definition of the entity"
        return page.meta

    def test_every_field_the_module_reads_is_a_field_of_the_core(self) -> None:
        own = {
            name for name, field in self._definition().fields.items() if field.owner is None
        }

        missing = sorted(set(self.uses) - own)

        assert not missing, f"{self.entity.__name__} no longer has {', '.join(missing)}"

    def test_the_core_publishes_a_contract_the_module_accepts(self) -> None:
        if not self.requires:
            return
        have = getattr(self.entity, "CONTRACT", None)
        assert have is not None, f"{self.entity.__name__} publishes no CONTRACT version"
        want_major, want_minor = (int(part) for part in self.requires.split(".")[:2])
        have_major, have_minor = (int(part) for part in str(have).split(".")[:2])
        assert (have_major, have_minor >= want_minor) == (want_major, True), (
            f"the module requires {self.entity.__name__} {self.requires} and the core "
            f"publishes {have}"
        )

    def test_the_definition_of_the_core_says_which_fields_are_the_modules(self) -> None:
        added = next(
            (one for one in extensions.under(self.entity) if one.entity is self.extension),
            None,
        )
        assert (
            added is not None
        ), f"{self.extension.__name__} is not mapped as an extension of {self.entity.__name__}"
        fields = self._definition().fields

        stray = [
            name for name in added.fields if fields[name].owner != self.extension.__name__
        ]

        assert (
            not stray
        ), f"fields of the module that the core's definition does not mark: {stray}"

    def test_a_record_of_the_extension_is_stored_read_back_as_itself_and_removed(
        self,
    ) -> None:
        store = self.make_store()
        record = blank(self.extension)
        record.id = new_entity_id()

        store.save(record)
        read = store.get(self.entity, record.id)
        store.remove(read)

        assert isinstance(read, self.extension)
        assert store.get(self.entity, record.id) is None
