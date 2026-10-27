"""`PublicationIds`: random ids, signed with the secret when there is one."""

import pytest

from sincpro_framework.data_layer.object_storage import PublicationRefused, Refusal
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds


def test_a_signed_id_is_unsigned_back_to_what_it_was_made_from():
    ids = PublicationIds("s3cr3t")

    raw, id = ids.new()

    assert len(raw) == 32 and id.startswith(f"{raw}.") and len(id) == 55
    assert ids.unsigned(id) == raw


@pytest.mark.parametrize(
    "forged",
    [
        lambda id: id[:-1] + ("x" if id[-1] != "x" else "y"),
        lambda id: id.partition(".")[0],
        lambda id: PublicationIds("other").signed(id.partition(".")[0]),
    ],
)
def test_an_id_not_made_here_is_forged(forged):
    ids = PublicationIds("s3cr3t")
    _, id = ids.new()

    with pytest.raises(PublicationRefused) as refused:
        ids.unsigned(forged(id))

    assert refused.value.reason is Refusal.FORGED


def test_without_a_secret_an_id_is_its_random_bits_alone():
    ids = PublicationIds()

    raw, id = ids.new()

    assert raw == id and ids.unsigned(id) == raw
