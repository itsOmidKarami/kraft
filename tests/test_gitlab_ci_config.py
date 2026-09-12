"""`.gitlab-ci.yml` parses to the shape GitLab actually accepts.

Written after a pipeline failed with zero jobs and no yaml_errors. The file was
valid YAML, which is why `yaml.safe_load` said nothing: an unquoted `: ` inside a
plain scalar makes the list item a *mapping* rather than a string, so

    - echo "declared: $CI_MERGE_REQUEST_LABELS"

parses as {'echo "declared': '$CI_MERGE_REQUEST_LABELS"'}. GitLab rejects the
whole config at pipeline creation, and the only symptom is a failed pipeline with
nothing in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[1] / ".gitlab-ci.yml"
SCRIPT_KEYS = ("script", "before_script", "after_script")


def _jobs():
    blob = yaml.safe_load(CONFIG.read_text())
    return {
        name: body
        for name, body in blob.items()
        if isinstance(body, dict) and any(k in body for k in SCRIPT_KEYS)
    }


def test_there_are_jobs_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert _jobs()


@pytest.mark.parametrize("key", SCRIPT_KEYS)
def test_every_script_line_is_a_string(key):
    bad = [
        (name, line)
        for name, body in _jobs().items()
        for line in (body.get(key) or [])
        if not isinstance(line, str)
    ]
    assert not bad, (
        f"{key} entries parsed as something other than a string - an unquoted "
        f"': ' makes YAML read the line as a mapping. Quote the whole line:\n"
        + "\n".join(f"  {name}: {line!r}" for name, line in bad)
    )


#: Paths deliberately absent from the repo -- a generated artifact, a
#: lockfile not yet committed. Empty today: nothing in .gitlab-ci.yml's
#: changes: rules is meant to miss.
ALLOWLIST: frozenset[str] = frozenset()


def _changes_paths() -> set[str]:
    return {
        pattern
        for body in _jobs().values()
        for rule in body.get("rules") or []
        for pattern in rule.get("changes") or []
    }


def _dead_paths(root: Path, patterns) -> list[str]:
    """Every pattern that matches no file under root."""
    return [p for p in patterns if not any(root.glob(p))]


def test_dead_paths_catches_a_glob_that_matches_nothing(tmp_path):
    (tmp_path / "real.txt").write_text("x")
    assert _dead_paths(tmp_path, {"real.txt"}) == []
    assert _dead_paths(tmp_path, {"nope/**/*"}) == ["nope/**/*"]


def test_every_changes_path_matches_something_in_the_repo():
    """A changes: glob matching nothing is a typo or a file that moved --
    Kraft-plyk found src/kraft/api.py long after it became a package. Either
    way the job silently falls through to `when: manual` and the pipeline
    goes green with no coverage."""
    dead = _dead_paths(CONFIG.parent, _changes_paths() - ALLOWLIST)
    assert not dead, (
        "changes: path matches nothing in the repo (typo, or the file moved):\n"
        + "\n".join(f"  {p}" for p in dead)
    )
