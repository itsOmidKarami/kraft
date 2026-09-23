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


def test_the_never_signal_steering_entry_gives_the_exact_shipped_yaml():
    """Kraft-c82sp: SAFETY_RULES left the code and became opt-in steering, so
    templates are seeded once and never overwritten -- an install first set up
    on 1.0.x has no `never-signal-processes-you-didnt-start` profile, and one
    upgraded from 0.x has the migrated profile but no repo naming it, because
    it used to be automatic. Either way the `how` an operator pastes in must
    be the *shipped* YAML, not a paraphrase that drifts from it."""
    [entry] = [
        c
        for c in capabilities.MANIFEST
        if c.version == "1.0.99" and c.name == "never_signal_steering"
    ]
    library = (ROOT / "templates" / "library.yaml").read_text()
    block = (
        "  never-signal-processes-you-didnt-start:\n"
        "    instructions: >-\n"
        "      Never signal a process you did not start. If something is already\n"
        "      listening on a port you need, it is not a stale leftover to clear -- it\n"
        "      might be the Kraft daemon serving other work right now. Check\n"
        "      $KRAFT_DAEMON_PID and $KRAFT_DAEMON_PORT in your environment before\n"
        "      touching anything you find on a port: if the pid or the port matches, it\n"
        "      is the daemon, and `kill`, `pkill`, or piping `lsof` into `xargs kill`\n"
        "      would take down orchestration for every other work item on this install,\n"
        "      including this one. Ask any server you start yourself for an ephemeral\n"
        "      port (bind port 0, or leave KRAFT_PORT unset) rather than reuse the\n"
        "      daemon's. If a task genuinely needs the daemon's own port, that is a\n"
        "      question for a human, not something to resolve by killing what is\n"
        "      already there.\n"
    )
    assert block in library
    assert block.rstrip("\n") in entry.how
    assert "steering: [never-signal-processes-you-didnt-start]" in entry.how
    # A direct check the operator can answer by looking at their own file --
    # not "skip this if migrate_files already ran", which asks them to recall
    # an internal mechanism instead of just reading library.yaml.
    assert (
        "if your library.yaml does not already have a "
        "`never-signal-processes-you-didnt-start` steering profile" in entry.how
    )
    # It must say plainly that the rule is no longer automatic, and that a
    # repo whose tests start servers should name it.
    assert "no longer" in entry.what and "automatically" in entry.what
    assert "start servers of their own should name it" in entry.what
