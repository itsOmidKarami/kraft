"""The previous-tag lookup release.yml runs before calling `next_tag.py`.

Nothing here can run the workflow itself -- it's a GitHub Actions job -- but the
regex it filters tags through is a plain, testable string. This pins that regex
against the two rc tags actually sitting on origin (v1.0.0.rc1, v1.0.0rc1) plus
the third shape a person might type (v1.0.0-rc.1), so a future edit that loosens
the filter back into matching a prerelease tag is caught here instead of in a
broken release.
"""

from __future__ import annotations

import re
from pathlib import Path

RELEASE_YML = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _previous_tag_pattern() -> str:
    """The regex `release.yml` greps reachable tags through, pulled out of the
    workflow text rather than duplicated by hand -- a change to one is a change
    to the other."""
    text = RELEASE_YML.read_text()
    match = re.search(r"grep -E '(\^v.+\$)'", text)
    assert match, "release.yml no longer greps the previous-tag list with -E; update this test"
    return match.group(1)


def test_previous_tag_pattern_accepts_exact_releases():
    pattern = _previous_tag_pattern()
    for tag in ["v0.3.2", "v1.0.0", "v10.2.33", "v0.0.1"]:
        assert re.fullmatch(pattern, tag), f"{tag!r} should be a candidate previous tag"


def test_previous_tag_pattern_rejects_every_prerelease_form():
    pattern = _previous_tag_pattern()
    for tag in ["v1.0.0rc1", "v1.0.0.rc1", "v1.0.0-rc.1"]:
        assert not re.fullmatch(pattern, tag), f"{tag!r} must not look like a previous release"


def _step(name: str) -> str:
    text = RELEASE_YML.read_text()
    start = text.index(f"- name: {name}")
    end = text.find("\n      - ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_only_a_stable_release_touches_homebrew_the_stamp_and_the_marketplace():
    for name in [
        "bump the homebrew tap",
        "mint a token to open the stamp PR",
        "stamp plugin manifests and CHANGELOG.md for this release",
        "publish the VS Code extension",
    ]:
        assert "steps.impact.outputs.pre == 'none'" in _step(name), name


def test_only_stable_and_rc_publish_to_pypi():
    assert """fromJSON('["none","rc"]')""" in _step("publish to PyPI")


def test_the_release_job_runs_in_the_main_only_environment():
    assert "    environment: release\n" in RELEASE_YML.read_text()
