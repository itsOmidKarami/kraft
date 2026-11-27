"""Helpers for the e2e tests that write to a real pull request or merge request
(`tests/adapters/forge/test_forge_thread_contract.py`). Not scanned by `check_tests`
rule (b), which flags a module-level helper in a test file that launches `gh`/`glab`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from support.harness import git


def gh_checkout(tmp_path: Path, slug: str) -> Path:
    """A folder `gh` reads `slug` from, as it reads a real checkout's.

    repo = gh_checkout(tmp_path, "owner/name")
    """
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", f"https://github.com/{slug}.git")
    return tmp_path


def cli_json(args: list[str], cwd: Path | None = None) -> object:
    """Run a real forge CLI for an e2e test; its JSON (None for an empty answer), or the
    test fails with its stderr.

        login = cli_json(["gh", "api", "user"])["login"]
    """
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout) if done.stdout.strip() else None
