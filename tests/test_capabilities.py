import pytest

from kraft import capabilities
from kraft.capabilities import Capability

#: The manifest is empty since Template Schema V1 (every earlier entry was
#: legacy-config instructions), so the mechanism is checked against a stand-in.
_MANIFEST = (
    Capability(version="1.2.0", name="a", what="does a", how="add a"),
    Capability(version="1.10.0", name="b", what="does b", how="add b"),
)


@pytest.fixture
def manifest(monkeypatch):
    monkeypatch.setattr(capabilities, "MANIFEST", _MANIFEST)
    return _MANIFEST


def test_manifest_entries_are_ordered_oldest_first():
    versions = [c.version for c in capabilities.MANIFEST]
    assert versions == sorted(versions, key=capabilities._key)


def test_every_entry_says_what_it_is_and_how_to_adopt_it():
    for c in capabilities.MANIFEST:
        assert c.name and c.what and c.how, c


def test_added_since_lists_only_strictly_newer_entries(manifest):
    """Numeric, not lexical: 1.10.0 is newer than 1.2.0."""
    assert capabilities.added_since("1.2.0") == [manifest[1]]


def test_added_since_is_empty_when_the_stamp_is_current(manifest):
    assert capabilities.added_since(manifest[-1].version) == []


def test_an_absent_stamp_means_everything_is_new(manifest):
    """A home seeded before stamping existed knows nothing about itself, so the
    honest answer is the whole manifest -- not an error, and not silence."""
    assert capabilities.added_since(None) == list(manifest)


def test_an_unparseable_stamp_is_treated_as_oldest_rather_than_raising(manifest):
    assert capabilities.added_since("not-a-version") == list(manifest)
