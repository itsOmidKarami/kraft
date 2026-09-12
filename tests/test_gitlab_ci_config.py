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

import sys
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


JUSTFILE = Path(__file__).resolve().parents[1] / "justfile"

#: `uv sync --frozen` installs the environment `just` recipes already assume
#: is there (`just setup`) — CI bootstraps it because a runner starts from
#: nothing; a local `just ci-test` does not need to repeat it, and no line in
#: the spec's own `ci-test` recipe (§1) does.
CI_TEST_SKIP: frozenset[str] = frozenset({"uv sync --frozen"})


def _recipe_body(name: str) -> list[str]:
    """Non-empty, non-comment lines of `just` recipe `name`'s body.

    A `just` recipe is a header line (`name:` or `name *ARGS:`) followed by
    lines indented deeper than column 0, ending at the next column-0 line or
    EOF. Good enough for this file's recipes, all of which are flat shell
    lines with no nested blocks.
    """
    lines = JUSTFILE.read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines) if line == f"{name}:" or line.startswith(f"{name} ")
    )
    body = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            body.append(stripped)
    return body


def test_recipe_body_stops_at_the_next_recipe(tmp_path, monkeypatch):
    """Pins `_recipe_body`'s own contract before trusting it against the real
    justfile: it must not run past the recipe it was asked for."""
    sample = tmp_path / "justfile"
    sample.write_text("a:\n    echo one\n    echo two\nb:\n    echo three\n")
    monkeypatch.setattr(sys.modules[__name__], "JUSTFILE", sample)
    assert _recipe_body("a") == ["echo one", "echo two"]
    assert _recipe_body("b") == ["echo three"]


def test_ci_test_recipe_covers_every_blocking_ci_script_line():
    """Kraft-579: verify's backend command must run what CI runs. Mirroring
    `.gitlab-ci.yml` by hand in `justfile` is exactly how the two drift; this
    is the drift detector. A CI step added to `lint-and-test` or `slow-tests`
    without a matching line landing in `ci-test` fails here instead of
    silently going unmeasured by `verify`."""
    jobs = _jobs()
    ci_lines = [
        line
        for job_name in ("lint-and-test", "slow-tests")
        for line in jobs[job_name].get("script") or []
        if line not in CI_TEST_SKIP
    ]
    assert ci_lines, "lint-and-test/slow-tests script: lines moved or were renamed"
    recipe = _recipe_body("ci-test")
    missing = [line for line in ci_lines if line not in recipe]
    assert not missing, (
        "justfile's ci-test recipe is missing CI script line(s) -- verify no "
        "longer measures what CI measures:\n" + "\n".join(f"  {line}" for line in missing)
    )


def test_ci_test_recipe_never_uses_testmon():
    """Kraft-44t0: `--testmon` exits 0 on an empty (change-selected) test
    selection, which is how four straight verify cycles on UI v2 Item actions
    reported green having run nothing. `ci-test` must never carry the flag
    back in."""
    assert "--testmon" not in " ".join(_recipe_body("ci-test"))
