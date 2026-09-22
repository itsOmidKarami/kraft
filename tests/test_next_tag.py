"""The tag a merge produces.

Pure functions, because everything else in this mechanism is a CI job that
cannot be run here. This is the part that can be wrong quietly.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

# `dev/` is not a package (no __init__.py). This test needs the functions
# rather than a script's exit code, so it loads the file directly instead of
# adding a package just for tests.
_SPEC = importlib.util.spec_from_file_location(
    "next_tag", Path(__file__).resolve().parents[1] / "dev" / "next_tag.py"
)
next_tag_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["next_tag"] = next_tag_mod
_SPEC.loader.exec_module(next_tag_mod)

impact_from_labels = next_tag_mod.impact_from_labels
next_tag = next_tag_mod.next_tag


@pytest.mark.parametrize(
    ("previous", "impact", "expected"),
    [
        ("v0.3.2", "patch", "v0.3.3"),
        ("v0.3.2", "minor", "v0.4.0"),
        ("v0.3.2", "major", "v1.0.0"),
        ("v1.9.9", "major", "v2.0.0"),
        (None, "patch", "v0.0.1"),
        (None, "minor", "v0.1.0"),
        (None, "major", "v1.0.0"),
        ("v0.3.2", "none", None),
    ],
)
def test_next_tag(previous, impact, expected):
    assert next_tag(previous, impact) == expected


def test_a_tag_without_three_components_is_refused():
    """Better to fail the job than to guess and tag something wrong."""
    with pytest.raises(ValueError):
        next_tag("v0.3", "patch")


@pytest.mark.parametrize(
    "previous",
    ["v1.0.0rc1", "v1.0.0.rc1", "v1.0.0-rc.1"],
    ids=["no-separator", "dot-separator", "dash-separator"],
)
def test_a_prerelease_tag_is_refused_by_name(previous):
    """release.yml's previous-tag lookup is meant to keep prerelease tags like
    these from ever reaching `next_tag`, but a repo can carry more than one
    malformed rc tag on the same commit (a ruleset can block deleting one), so
    this is the last line of defence: the error must name the exact tag it
    choked on, not just say "a tag" in the abstract.
    """
    with pytest.raises(ValueError, match=re.escape(previous)):
        next_tag(previous, "patch")


def test_an_unknown_impact_is_refused():
    with pytest.raises(ValueError):
        next_tag("v0.3.2", "enormous")


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ("release::minor", "minor"),
        ("bug,release::patch,frontend", "patch"),
        ("release::none", "none"),
        ("bug,frontend", None),
        ("", None),
    ],
)
def test_impact_from_labels(labels, expected):
    assert impact_from_labels(labels) == expected


def test_two_release_labels_raise():
    """GitHub cannot enforce one-of, so this is where the rule lives."""
    with pytest.raises(ValueError, match="more than one"):
        impact_from_labels("release::minor,release::patch")


def test_one_release_label_among_others_still_reads():
    assert impact_from_labels("bug,release::patch,needs-review") == "patch"


def test_no_release_label_is_none():
    assert impact_from_labels("bug,needs-review") is None
