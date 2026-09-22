from pathlib import Path

import pytest

from kraft import capabilities
from kraft.capabilities import Capability

ROOT = Path(__file__).resolve().parents[1]

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


def test_the_mr_rebase_entry_gives_the_exact_shipped_yaml():
    """Kraft-3llig review fix 2: the capability shipped invisibly to every
    existing install without one -- the `how` an operator pastes in must be
    the *shipped* YAML, not a paraphrase that drifts from it."""
    [entry] = [c for c in capabilities.MANIFEST if c.version == "1.0.3" and c.name == "mr_rebase"]
    library = (ROOT / "templates" / "library.yaml").read_text()
    chain = (ROOT / "templates" / "chains" / "default.yaml").read_text()
    assert "  mr_rebase:\n    kind: builtin\n    ref: kraft.mr_rebase\n" in library
    assert "  mr_rebase:\n    kind: builtin\n    ref: kraft.mr_rebase\n" in entry.how
    steps = (
        "    steps:\n"
        "      - id: rebase\n"
        "        tasks:\n"
        "          - id: rebase\n"
        "            extends: mr_rebase\n"
        "      - id: open\n"
        "        tasks:\n"
        "          - id: open\n"
        "            extends: open_draft_mr\n"
    )
    assert steps in chain
    assert steps.rstrip("\n") in entry.how


@pytest.mark.parametrize("seeded", ["1.0.0", "1.0.1", "1.0.2"])
def test_every_install_seeded_before_the_rebase_shipped_is_told_about_it(seeded):
    """`mr_rebase` shipped in 1.0.3; 1.0.1 and 1.0.2 went out without it, so an
    install first seeded by either has a `draft_merge_request` with no rebase
    and must still be told (`added_since` is strictly newer)."""
    assert "mr_rebase" in [c.name for c in capabilities.added_since(seeded)]
