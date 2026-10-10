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

_JOBS = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "test.yml").read_text()
)["jobs"]


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
    """What the `changes` job writes to its outputs for `base..HEAD` in `repo`."""
    out = repo.parent / "github-output"
    ran = _bash(
        _script("changes"),
        {"GITHUB_EVENT_NAME": event, "BASE": base, "GITHUB_OUTPUT": str(out)},
        cwd=repo,
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

    assert _changes(repo, base, event) == [f"code={code}", f"python={python}"]


@pytest.mark.parametrize("to", ["frontend/calc.py", "docs/calc.md"])
def test_a_python_file_moved_into_a_skipped_tree_is_still_a_python_change(tmp_path, to):
    """git names a rename by its new path alone, which would read as a change
    to frontend/ or to the docs and nothing else; the file left `src` too."""
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / to).parent.mkdir()
    git(repo, "mv", "calc.py", to)
    commit_all(repo)

    assert _changes(repo, base, "pull_request") == ["code=true", "python=true"]


_DOCS_ONLY_SKIPS = ("test", "e2e-cli", "frontend", "vscode", "e2e", "ui-contract")


@pytest.mark.parametrize(
    ("code", "python", "results", "merges"),
    [
        pytest.param("true", "true", {"docs-tests": "skipped"}, True, id="a-python-change"),
        pytest.param(
            "true",
            "false",
            {"test": "skipped", "e2e-cli": "skipped"},
            True,
            id="no-python-changed",
        ),
        pytest.param(
            "false", "false", dict.fromkeys(_DOCS_ONLY_SKIPS, "skipped"), True, id="docs-only"
        ),
        pytest.param(
            "true",
            "false",
            {"test": "skipped", "e2e-cli": "skipped", "docs-tests": "skipped"},
            False,
            id="no-python-changed-and-nothing-stood-in",
        ),
        pytest.param(
            "true",
            "false",
            {"test": "skipped", "e2e-cli": "skipped", "frontend": "skipped"},
            False,
            id="no-python-changed-but-frontend-skipped",
        ),
        pytest.param(
            "true",
            "true",
            {"docs-tests": "skipped", "test": "skipped"},
            False,
            id="a-python-change-whose-shards-skipped",
        ),
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
    needs = {job: {"result": results.get(job, "success")} for job in _JOBS["test-gate"]["needs"]}
    assert set(results) <= set(needs)

    ran = _bash(_script("test-gate"), {"CODE": code, "PYTHON": python, "NEEDS": json.dumps(needs)})

    assert (ran.returncode == 0) is merges, ran.stdout + ran.stderr
