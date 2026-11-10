"""The two shell scripts in `.github/workflows/test.yml` that decide what a
pull request runs and whether it may merge, run here the way a runner runs
them: `changes`, which reads the changed paths, and the `test` gate, which
reads every job's result. A wrong answer from either is a red change that
merges green, and nothing else in the suite executes them."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import commit_all, git, make_repo, write

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_JOBS = yaml.safe_load((_WORKFLOWS / "test.yml").read_text())["jobs"]


def _script(job: str) -> str:
    """The job's one multi-line `run:` step."""
    (script,) = [s["run"] for s in _JOBS[job]["steps"] if "\n" in s.get("run", "")]
    return script


def _bash(script: str, env: dict[str, str], cwd: Path | None = None):
    # `bash -e`: what a runner gives a `run:` step that names no shell.
    return subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _changes(repo: Path, base: str, event: str) -> list[str]:
    """What the `changes` job writes to its outputs for `base..HEAD` in `repo`,
    run in a one-commit clone of it, as the job's shallow checkout is."""
    out = repo.parent / "github-output"
    clone = repo.parent / "checkout"
    git(repo.parent, "clone", "--quiet", "--depth=1", f"file://{repo}", str(clone))
    ran = _bash(
        _script("changes"),
        {"GITHUB_EVENT_NAME": event, "BASE": base, "GITHUB_OUTPUT": str(out)},
        cwd=clone,
    )
    assert ran.returncode == 0, ran.stderr
    return out.read_text().split()


@pytest.mark.parametrize(
    ("event", "paths", "code", "python"),
    [
        pytest.param(
            "pull_request",
            ["README.md", "docsite/content/a.md", "docs/testing.md", ".github/assets/a.png"],
            "false",
            "false",
            id="docs-only",
        ),
        pytest.param("pull_request", ["frontend/src/a.tsx"], "true", "false", id="frontend-only"),
        pytest.param(
            "pull_request",
            ["frontend/src/a.tsx", "docsite/content/a.md"],
            "true",
            "false",
            id="frontend-and-docs",
        ),
        pytest.param(
            "pull_request",
            ["frontend/src/a.tsx", "src/kraft/a.py"],
            "true",
            "true",
            id="frontend-and-python",
        ),
        pytest.param("pull_request", ["tests/test_a.py"], "true", "true", id="a-test"),
        pytest.param(
            "pull_request", ["frontend/e2e/serve.py"], "true", "true", id="python-under-frontend"
        ),
        pytest.param("pull_request", ["docs/intent/a.md"], "true", "true", id="an-intent-file"),
        pytest.param("pull_request", ["vscode/src/a.ts"], "true", "true", id="vscode"),
        pytest.param("pull_request", ["frontendish/a.md"], "true", "true", id="not-quite-frontend"),
        pytest.param("pull_request", ["uv.lock"], "true", "true", id="the-lockfile"),
        pytest.param("push", ["README.md"], "true", "true", id="a-push-runs-everything"),
    ],
)
def test_changes_says_which_jobs_a_change_needs(tmp_path, event, paths, code, python):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    for path in paths:
        write(repo, path, "x\n")
    commit_all(repo)

    assert _changes(repo, base, event)[:2] == [f"code={code}", f"python={python}"]


@pytest.mark.parametrize("to", ["frontend/calc.py", "docs/calc.md"])
def test_a_python_file_moved_into_a_skipped_tree_is_still_a_python_change(tmp_path, to):
    """git names a rename by its new path alone, which would read as a change
    to frontend/ or to the docs and nothing else; the file left `src` too."""
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / to).parent.mkdir()
    git(repo, "mv", "calc.py", to)
    commit_all(repo)

    assert _changes(repo, base, "pull_request")[:2] == ["code=true", "python=true"]


@pytest.mark.parametrize(
    ("event", "paths", "pythons"),
    [
        pytest.param(
            "push", ["README.md"], ["3.12", "3.13", "3.14", "3.15"], id="a-push-runs-all-four"
        ),
        pytest.param(
            "pull_request", ["src/kraft/a.py"], ["3.12", "3.14"], id="a-pr-runs-floor-and-3.14"
        ),
        pytest.param(
            "pull_request", ["frontend/src/a.tsx"], ["3.14"], id="no-python-changed-runs-3.14"
        ),
        pytest.param("pull_request", ["docs/testing.md"], ["3.14"], id="docs-only-runs-3.14"),
    ],
)
def test_changes_picks_the_pythons_the_unit_tier_runs(tmp_path, event, paths, pythons):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    for path in paths:
        write(repo, path, "x\n")
    commit_all(repo)

    # No spaces: the value is one `name=value` line in $GITHUB_OUTPUT.
    assert _changes(repo, base, event)[2] == f"pythons={json.dumps(pythons, separators=(',', ':'))}"


def test_the_unit_tier_runs_the_pythons_changes_picked_and_all_four_if_it_failed():
    """Without this the `pythons` output is computed and never read; an empty
    output (the job failed) must fall back to every supported Python, so a
    failed `changes` trims nothing."""
    assert _JOBS["test"]["strategy"]["matrix"]["python-version"] == (
        '${{ fromJSON(needs.changes.outputs.pythons || \'["3.12", "3.13", "3.14", "3.15"]\') }}'
    )


_NEEDS = _JOBS["test-gate"]["needs"]
_DOCS_ONLY = dict.fromkeys(
    ("test", "e2e-cli", "e2e-macos", "frontend", "vscode", "e2e", "ui-contract"), "skipped"
)
#: What skips, and passes, by the kind of change: (code, python, the skips).
_PYTHON_CHANGE = ("true", "true", {"docs-tests": "skipped"})
_NO_PYTHON = (
    "true",
    "false",
    {"docs-tests": "skipped", "e2e-cli": "skipped", "e2e-macos": "skipped"},
)


@pytest.mark.parametrize(
    ("code", "python", "results", "merges"),
    [
        pytest.param(*_PYTHON_CHANGE, True, id="a-python-change"),
        pytest.param(*_NO_PYTHON, True, id="no-python-changed"),
        pytest.param("false", "false", _DOCS_ONLY, True, id="docs-only"),
        pytest.param(
            "false",
            "false",
            {**_DOCS_ONLY, "docs-tests": "skipped"},
            False,
            id="docs-only-and-nothing-stood-in",
        ),
        # A job may skip only where the change asked it to: every other skip fails.
        *[
            pytest.param(
                code, python, {**skips, job: "skipped"}, False, id=f"{job}-skipped-on-{kind}"
            )
            for kind, (code, python, skips) in {
                "a-python-change": _PYTHON_CHANGE,
                "no-python-changed": _NO_PYTHON,
                "docs-only": ("false", "false", _DOCS_ONLY),
            }.items()
            for job in _NEEDS
            if job not in skips and job != "docs-tests"
        ],
        pytest.param(
            "true", "true", {"docs-tests": "skipped", "lint": "failure"}, False, id="a-failed-job"
        ),
        pytest.param(
            "true",
            "true",
            {"docs-tests": "skipped", "e2e": "cancelled"},
            False,
            id="a-cancelled-job",
        ),
        pytest.param(
            "",
            "",
            {"docs-tests": "skipped", "changes": "failure"},
            False,
            id="changes-itself-failed",
        ),
    ],
)
def test_the_gate_passes_only_the_skips_the_change_asked_for(code, python, results, merges):
    needs = {job: {"result": results.get(job, "success")} for job in _NEEDS}
    assert set(results) <= set(needs)

    ran = _bash(_script("test-gate"), {"CODE": code, "PYTHON": python, "NEEDS": json.dumps(needs)})

    assert (ran.returncode == 0) is merges, ran.stdout + ran.stderr
    # and it failed on a job's result, not on a missing jq
    assert ("::error::" in ran.stdout) is not merges, ran.stdout + ran.stderr


def test_removals_are_judged_against_the_base_github_merged_into():
    """`pull_request.base.sha` stays at the commit a pull request was opened
    from while the job checks out its merge into today's `main`, so every
    test `main` removed since read as the pull request's own (#1132). The
    merge commit's first parent is the base it was actually merged into."""
    (step,) = [
        s for s in _JOBS["removals-declared"]["steps"] if "check_removals" in s.get("run", "")
    ]
    assert 'check_removals.py "$(git rev-parse HEAD^1)"' in step["run"]
    assert "base.sha" not in str(step)


def test_the_shard_count_is_the_same_everywhere_the_workflow_says_it():
    """The matrix lists the shards; the job name and each `KRAFT_TEST_SHARD` repeat
    how many there are. A matrix of three with `/4` in the variable runs three
    quarters of the suite, and passes."""
    job = _JOBS["test"]
    shards = job["strategy"]["matrix"]["shard"]
    count = len(shards)
    assert shards == list(range(1, count + 1))
    assert job["name"].endswith("${{ matrix.shard }}/" + f"{count})")
    variables = [
        step["env"]["KRAFT_TEST_SHARD"]
        for step in job["steps"]
        if "KRAFT_TEST_SHARD" in step.get("env", {})
    ]
    assert variables
    assert set(variables) == {"${{ matrix.shard }}/" + str(count)}


def test_a_full_history_checkout_is_blobless():
    """`fetch-depth: 0` alone fetches every branch and tag in full, mostly the
    screenshots of branches that never merge, and one checkout took five minutes
    on a slow day. `filter: blob:none` keeps the history and drops the bytes.
    `release.yml` keeps its full clone: it publishes, and a lazy fetch that fails
    there is not worth the half minute."""
    full = [
        f"{path.name}: {job}"
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        if path.name != "release.yml"
        for job, spec in yaml.safe_load(path.read_text())["jobs"].items()
        for step in spec.get("steps", [])
        if step.get("uses", "").startswith("actions/checkout")
        and step.get("with", {}).get("fetch-depth") == 0
        and step["with"].get("filter") != "blob:none"
    ]
    assert full == []
