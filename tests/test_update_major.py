"""`kraft.update` across a major release: the 1.5.0 candidates, which shipped
as 2.0, and 2.0's own. A sibling of `tests/test_update.py`, which is at its
line budget.

Every comparison here is on the whole version, so none of it is special to 2.0;
these pin the one crossing that really happens, with the tags as they exist."""

from __future__ import annotations

import pytest

from kraft import update


def _release(tag):
    wheel = [{"name": "k.whl", "browser_download_url": "https://x/k.whl"}]
    return {"tag_name": tag, "draft": False, "prerelease": "rc" in tag, "assets": wheel}


#: The feed as 2.0's first candidate leaves it: the fourteen 1.5.0 candidates
#: were the pre-releases of what ships as 2.0.0, and no 1.5.0 final exists.
CROSSING = [
    _release("v2.0.0rc1"),
    *(_release(f"v1.5.0rc{n}") for n in range(14, 0, -1)),
    _release("v1.4.0"),
]


def test_the_rc_channel_crosses_from_the_1_5_candidates_to_2_0():
    """A new major's rc1 outranks the old line's rc14: the version is compared
    whole, never by its pre-release number or where the feed lists it."""
    assert update._parse(CROSSING, "rc").tag == "v2.0.0rc1"
    assert update._parse(list(reversed(CROSSING)), "rc").tag == "v2.0.0rc1"
    assert update._parse(CROSSING, "stable").tag == "v1.4.0"


@pytest.mark.parametrize(
    ("here", "there", "behind"),
    [
        ("1.5.0rc14", "v2.0.0rc1", True),
        ("2.0.0rc1", "v1.5.0rc14", False),
        ("1.4.0", "v2.0.0", True),
    ],
)
def test_a_1_5_candidate_is_behind_2_0(monkeypatch, here, there, behind):
    monkeypatch.setattr(update, "installed", lambda: here)
    assert update.is_behind(update.Release(tag=there, wheel_url="u")) is behind


def test_a_rollback_from_2_0_to_a_1_5_candidate_is_older():
    assert update.is_older("1.5.0rc14", "2.0.0rc1") is True
    assert update.is_older("2.0.0rc1", "1.5.0rc14") is False


def test_a_1_5_candidate_follows_the_rc_channel():
    assert update.channel_of("1.5.0rc14") == "rc"
