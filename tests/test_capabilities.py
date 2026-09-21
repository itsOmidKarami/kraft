from kraft import capabilities


def test_manifest_entries_are_ordered_oldest_first():
    versions = [c.version for c in capabilities.MANIFEST]
    assert versions == sorted(versions, key=capabilities._key)


def test_every_entry_says_what_it_is_and_how_to_adopt_it():
    for c in capabilities.MANIFEST:
        assert c.name and c.what and c.how, c


def test_added_since_lists_only_strictly_newer_entries():
    found = capabilities.added_since("0.60.0")
    assert found
    assert all(capabilities._key(c.version) > capabilities._key("0.60.0") for c in found)


def test_added_since_is_empty_when_the_stamp_is_current():
    newest = capabilities.MANIFEST[-1].version
    assert capabilities.added_since(newest) == []


def test_an_absent_stamp_means_everything_is_new():
    """A home seeded before stamping existed knows nothing about itself, so the
    honest answer is the whole manifest -- not an error, and not silence."""
    assert capabilities.added_since(None) == list(capabilities.MANIFEST)


def test_an_unparseable_stamp_is_treated_as_oldest_rather_than_raising():
    assert capabilities.added_since("not-a-version") == list(capabilities.MANIFEST)


def test_steps_is_advertised_to_installs_that_predate_it():
    names = [c.name for c in capabilities.MANIFEST]
    assert "steps" in names
    entry = next(c for c in capabilities.MANIFEST if c.name == "steps")
    assert "steps:" in entry.how


def test_the_inputs_capability_is_announced():
    """Announced as inert under V1: no task declares inputs, and an operator
    who configures the old key is told it buys nothing, rather than told how."""
    entry = next((c for c in capabilities.MANIFEST if c.name == "inputs"), None)
    assert entry is not None
    assert "INERT" in entry.what
    assert entry.how.startswith("nothing to adopt")


def test_the_rebase_chain_shape_is_announced():
    """doctor's node-id diff cannot see removed or reshaped nodes
    (Kraft-rl34g); the manifest is the only thing that tells the operator."""
    entry = next(c for c in capabilities.MANIFEST if c.name == "rebase steps")
    assert entry.version == "0.74.0"
    assert "on.mr.rebase" in entry.how
    assert "on.env.prepare" in entry.how
