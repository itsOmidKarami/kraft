"""How Kraft draws an id, and how a session is named outside its item."""

from __future__ import annotations

import pytest

from kraft import ids


@pytest.mark.parametrize(
    "length", [ids.WORK_ITEM_LENGTH, ids.SESSION_LENGTH], ids=["work-item", "session"]
)
def test_an_id_is_drawn_from_lowercase_crockford_base32(length):
    """Every character of the alphabet turns up in 200 draws (a missing one
    has odds under 1e-15), so a draw from part of it, which would shrink the
    40 and 30 bits a collision needs, fails too."""
    drawn = [ids.mint(length, lambda _c: False) for _ in range(200)]

    assert all(len(d) == length for d in drawn)
    assert set("".join(drawn)) == set(ids.ALPHABET)
    assert set(ids.ALPHABET) == set("0123456789abcdefghjkmnpqrstvwxyz")


def test_a_taken_candidate_is_drawn_again():
    seen = []

    def taken(candidate):
        seen.append(candidate)
        return len(seen) < 3

    assert ids.mint(8, taken) == seen[-1]
    assert len(seen) == 3


def test_ten_taken_candidates_in_a_row_give_up_and_say_why():
    asked = []

    def taken(candidate):
        asked.append(candidate)
        return True

    with pytest.raises(ids.Exhausted, match="10 candidate ids in a row were taken"):
        ids.mint(8, taken)
    assert len(asked) == 10


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("k7m2x9qa/p3n8w2", ("k7m2x9qa", "p3n8w2")),
        ("8cbfe6e27c1044b3e445c0f0d726357d", (None, "8cbfe6e27c1044b3e445c0f0d726357d")),
    ],
    ids=["qualified", "pre-upgrade-bare"],
)
def test_a_session_reference_names_its_item(value, parsed):
    assert ids.parse_session_ref(value) == parsed
    if parsed[0]:
        assert ids.session_ref(*parsed) == value


@pytest.mark.parametrize(
    "value",
    ["", "k7m2x9qa/", "/p3n8w2", "k7m2x9qa/p3n8w2/x", "k7m2x9qa//p3n8w2"],
    ids=["empty", "no-session", "no-item", "a-second-slash", "an-empty-segment"],
)
def test_a_malformed_session_reference_names_no_session(value):
    """Refused whole, never split some way that happens to find a session."""
    with pytest.raises(ValueError, match="not a session reference"):
        ids.parse_session_ref(value)


@pytest.mark.parametrize(
    ("wid", "sid", "name"),
    [
        ("k7m2x9qa", "p3n8w2", "k7m2x9qa-p3n8w2"),
        ("8cbfe6e27c1044b3e445c0f0d726357d", "p3n8w2", "8cbfe6e27c1044b3e445c0f0d726357d-p3n8w2"),
        ("8cbfe6e27c1044b3e445c0f0d726357d", "aa" * 16, "aa" * 16),
    ],
    ids=["new-item-new-session", "old-item-new-session", "old-session"],
)
def test_a_machine_wide_name_keeps_an_old_session_s_form(wid, sid, name):
    assert ids.qualified(wid, sid) == name
