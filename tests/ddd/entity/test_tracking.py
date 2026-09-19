"""`ChangeTrackingMixin` consolidates whatever a Feature touched into one `Updated` event,
instead of one per field it set — proved here against `MemoryRepository`, and against the
SQLAlchemy `Repository` in `tests/orm/test_tracking.py`.
"""

from dataclasses import dataclass, field

from sincpro_framework.ddd.entity import (
    ArchivableMixin,
    AuditedMixin,
    ChangeTrackingMixin,
    Entity,
    EntityUpdated,
)
from sincpro_framework.ddd.repositories.memory_repository import MemoryRepository


@dataclass
class Note(ChangeTrackingMixin, Entity):
    title: str
    body: str = ""
    render_cache: str = field(default="", metadata={"tracked": False})


def test_the_generic_event_carries_an_explicit_wire_name():
    assert EntityUpdated.name == "ddd.entity.v1.updated"


def test_tracked_fields_excludes_entity_bookkeeping_and_opted_out_fields():
    assert Note.tracked_fields() == {"title", "body"}


def test_nothing_is_recorded_on_the_first_save():
    repository = MemoryRepository()
    note = Note(title="first")

    repository.save(note)

    assert note.pull_events() == []


def test_one_event_carries_every_field_that_changed():
    repository = MemoryRepository()
    note = Note(title="before", body="old")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "after"
    loaded.body = "new"
    repository.save(loaded)

    events = loaded.pull_events()
    assert len(events) == 1
    assert events[0].name == "ddd.entity.v1.updated"
    assert events[0].changes == {"title": ("before", "after"), "body": ("old", "new")}
    assert events[0].entity_type == "Note" and events[0].entity_id == note.id


def test_excluded_field_never_shows_up_in_the_diff():
    repository = MemoryRepository()
    note = Note(title="x")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.render_cache = "rebuilt"
    repository.save(loaded)

    assert loaded.pull_events() == []


def test_setting_a_field_back_to_its_original_value_nets_to_no_change():
    repository = MemoryRepository()
    note = Note(title="original")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "draft"
    loaded.title = "original"
    repository.save(loaded)

    assert loaded.pull_events() == []


def test_a_second_save_in_the_same_object_compares_against_the_last_persisted_state():
    repository = MemoryRepository()
    note = Note(title="v1")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "v2"
    repository.save(loaded)
    first_round = loaded.pull_events()

    loaded.title = "v3"
    repository.save(loaded)
    second_round = loaded.pull_events()

    assert first_round[0].changes == {"title": ("v1", "v2")}
    assert second_round[0].changes == {"title": ("v2", "v3")}


def test_a_domain_specific_event_class_can_be_declared_instead():
    @dataclass(kw_only=True)
    class TitleChanged(EntityUpdated):
        pass

    @dataclass
    class Renamed(ChangeTrackingMixin, Entity):
        title: str
        change_event = TitleChanged

    repository = MemoryRepository()
    thing = Renamed(title="a")
    repository.save(thing)

    loaded = repository.get(Renamed, thing.id)
    loaded.title = "b"
    repository.save(loaded)

    events = loaded.pull_events()
    assert len(events) == 1 and isinstance(events[0], TitleChanged)


def test_a_freshly_built_entity_starts_compared_against_its_own_constructor_values():
    note = Note(title="from the constructor")

    assert note.changes() == {}

    note.title = "changed before ever being saved"

    assert note.changes() == {
        "title": ("from the constructor", "changed before ever being saved")
    }


def test_search_snapshots_every_row_it_hands_back():
    repository = MemoryRepository()
    note = Note(title="a")
    repository.save(note)

    from sincpro_framework.ddd.entity.entity_collection import EntityCollection

    class Notes(EntityCollection[Note]):
        pass

    page = repository.search(Notes)
    found = page.items[0]
    found.title = "b"
    repository.save(found)

    assert found.pull_events()[0].changes == {"title": ("a", "b")}


def test_archived_at_is_reserved_like_any_entity_bookkeeping_field():
    @dataclass
    class Client(ChangeTrackingMixin, AuditedMixin, ArchivableMixin, Entity):
        name: str

    assert Client.tracked_fields() == {"name"}


def test_the_explicit_mode_hands_the_event_back_to_whoever_asked():
    """No repository in sight: the caller consolidates the change itself and decides what to
    do with the event — store it, publish it, or neither."""
    repository = MemoryRepository()
    note = Note(title="before")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "after"

    event = loaded.record_changes()

    assert event is not None
    assert event.changes == {"title": ("before", "after")}
    assert event.entity_type == "Note" and event.entity_id == note.id


def test_the_explicit_mode_answers_nothing_when_nothing_changed():
    repository = MemoryRepository()
    note = Note(title="untouched")
    repository.save(note)

    loaded = repository.get(Note, note.id)

    assert loaded.record_changes() is None


def test_what_comes_back_is_the_same_event_pull_events_hands_over():
    repository = MemoryRepository()
    note = Note(title="before")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "after"
    event = loaded.record_changes()

    pending = loaded.pull_events()
    assert pending == [event]  # one event, not two: publish one or the other


def test_recording_by_hand_moves_the_baseline_so_the_next_save_is_clean():
    repository = MemoryRepository()
    note = Note(title="before")
    repository.save(note)

    loaded = repository.get(Note, note.id)
    loaded.title = "after"
    loaded.record_changes()
    loaded.pull_events()

    repository.save(loaded)  # the automatic hook runs over an already-consolidated change

    assert loaded.pull_events() == []


@dataclass
class Line(Entity):
    amount: int = 0


@dataclass
class Size:
    width: int = 1


@dataclass
class Order(ChangeTrackingMixin, Entity):
    """One of each kind beside a plain field: a value object, a to-one and a to-many."""

    reference: str = ""
    size: Size = field(default_factory=Size)
    owner: Line | None = None
    lines: list[Line] = field(default_factory=list)


def test_a_field_pointing_at_another_aggregate_is_never_tracked():
    """A relation is a different aggregate, not a value of this one — and tracking it would
    put whole records inside the event, growing with the relation."""
    assert Order.tracked_fields() == {"reference", "size"}


def test_changing_a_relation_produces_no_event_but_changing_a_value_object_does():
    repository = MemoryRepository()
    order = Order(reference="SO-1")
    repository.save(order)

    loaded = repository.get(Order, order.id)
    loaded.lines = [Line(amount=10)]
    loaded.owner = Line(amount=20)

    assert loaded.record_changes() is None  # a relation moved; this aggregate did not

    loaded.size = Size(width=9)
    event = loaded.record_changes()

    assert event is not None
    assert event.changes == {"size": (Size(width=1), Size(width=9))}


def test_a_value_object_in_an_event_survives_the_framework_serializer():
    repository = MemoryRepository()
    order = Order(reference="SO-2")
    repository.save(order)

    loaded = repository.get(Order, order.id)
    loaded.size = Size(width=9)
    event = loaded.record_changes()

    assert event is not None
    assert '"width":9' in event.as_json().replace(" ", "")


def test_the_consolidated_event_joins_the_chain_of_whatever_caused_it():
    """The automatic mode records an uncorrelated event — the aggregate has no ambient request
    to read. The Feature that pulls it knows the incoming event, and chains it there."""
    repository = MemoryRepository()
    note = Note(title="before")
    repository.save(note)

    incoming = EntityUpdated(changes={}, correlation_id="req-42")

    loaded = repository.get(Note, note.id)
    loaded.title = "after"
    repository.save(loaded)

    published = [event.caused_by(incoming) for event in loaded.pull_events()]

    assert len(published) == 1
    assert published[0].correlation_id == "req-42"
    assert published[0].causation_id == incoming.id
    assert published[0].entity_id == note.id  # the envelope record() stamped is still there


# --- what a person reads when the event is opened -----------------------------------------


@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    """One field that declares a label, one that does not."""

    total: int = field(default=0, metadata={"label": {"default": "Total", "es": "Importe"}})
    memo: str = ""


def _updated(**moved: object) -> EntityUpdated:
    repository = MemoryRepository()
    invoice = Invoice(total=0)
    repository.save(invoice)
    loaded = repository.get(Invoice, invoice.id)
    for name, value in moved.items():
        setattr(loaded, name, value)
    repository.save(loaded)
    return loaded.pull_events()[0]


def test_the_event_says_in_both_languages_that_it_was_updated():
    """The whole point: somebody opening the event reads a sentence, not a class name."""
    assert _updated(total=500).label == {"default": "Updated", "es": "Se actualizó"}


def test_the_event_names_what_was_updated_without_repeating_it_in_the_label():
    event = _updated(total=500)
    assert event.entity_type == "Invoice"
    assert "Invoice" not in event.label["default"]


def test_spanish_is_a_verb_so_it_never_has_to_agree_in_gender():
    """ "Factura actualizado" is what an adjective would produce half the time."""
    assert _updated(total=500).label["es"] == "Se actualizó"


def test_a_subclass_can_say_it_in_its_own_words():
    from typing import ClassVar

    @dataclass(kw_only=True)
    class InvoiceSettled(EntityUpdated):
        name = "billing.invoice.v1.settled"
        label: dict[str, str] = field(
            default_factory=lambda: {"default": "Settled", "es": "Se canceló"}
        )

    @dataclass
    class Settling(Invoice):
        change_event: ClassVar[type] = InvoiceSettled

    repository = MemoryRepository()
    invoice = Settling(total=0)
    repository.save(invoice)
    loaded = repository.get(Settling, invoice.id)
    loaded.total = 900
    repository.save(loaded)

    assert loaded.pull_events()[0].label["es"] == "Se canceló"


def test_the_event_carries_the_label_of_each_field_that_moved():
    assert _updated(total=500).field_labels == {
        "total": {"default": "Total", "es": "Importe"}
    }


def test_a_field_that_declared_no_label_is_simply_absent():
    """There is nothing to say about it — an empty dict would claim there was."""
    event = _updated(memo="something")
    assert "memo" in event.changes
    assert event.field_labels == {}


def test_only_the_fields_that_moved_are_labelled_not_the_whole_model():
    """A change to one field must not drag the entire model's vocabulary into the event."""
    event = _updated(total=500)
    assert set(event.field_labels) == {"total"} == set(event.changes)


def test_the_words_are_frozen_at_the_moment_it_happened():
    """Rename the label afterwards and the recorded event keeps the old word — an audit says
    what a person saw then, and resolving it at read time would rewrite history."""
    event = _updated(total=500)

    Invoice.__dataclass_fields__["total"].metadata = {"label": {"default": "Amount"}}
    try:
        assert event.field_labels["total"]["default"] == "Total"
    finally:
        Invoice.__dataclass_fields__["total"].metadata = {
            "label": {"default": "Total", "es": "Importe"}
        }


# --- the consolidated event sees what the rules did, not what they were about to do --------


def test_a_field_a_rule_computes_is_in_the_same_event_with_the_value_it_ended_on():
    """The store's own bookkeeping closes `before_save`, after every rule. Run first, it took
    the diff and moved the baseline while a rule was still about to write — so the computed
    field missed this event and turned up in the next one carrying a value the aggregate no
    longer held. An audit that states a fact that never happened is worse than a silent one.
    """
    from sincpro_framework.ddd.repositories.repository import Rule

    @dataclass
    class Article(ChangeTrackingMixin, Entity):
        title: str = ""
        slug: str = ""

    def derive_slug(article: Article) -> None:
        article.slug = article.title.lower().replace(" ", "-")

    repository = MemoryRepository(rules=[Rule(entity=Article, before_save=derive_slug)])
    article = Article(title="First")
    repository.save(article)
    article.pull_events()

    said: list[dict] = []
    for heading in ("Second Title", "Third"):
        loaded = repository.get(Article, article.id)
        loaded.title = heading
        repository.save(loaded)
        said.append(loaded.pull_events()[0].changes)
        stored = repository.get(Article, article.id)
        # what the event claims the field became is what the store actually holds
        assert said[-1]["slug"][1] == stored.slug

    assert said[0]["slug"] == ("", "second-title")
    assert said[1]["slug"] == ("second-title", "third")
