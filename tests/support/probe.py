"""Probing a repository in a test: a committed copy of the sample repo with
files on top, and the proposal the packaged detector table makes for it."""

from __future__ import annotations

from pathlib import Path

from kraft import detect
from support.harness import commit_all, make_repo

#: A pyproject that names pytest, so a Python toolchain proposes it.
PYTEST = "[project]\nname = 'x'\n[tool.pytest.ini_options]\n"
#: A package.json with a real `test` script.
JEST = '{"scripts": {"test": "jest"}}'


def repo_with(tmp_path: Path, files: dict[str, str], executable: tuple[str, ...] = ()) -> Path:
    """`make_repo` with `files` written and committed: the probe reads the
    committed tree, so an uncommitted file is invisible to it. For example
    `repo_with(tmp_path, {"go.mod": "module x\\n"})`.
    """
    repo = make_repo(tmp_path)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    for rel in executable:
        (repo / rel).chmod(0o755)
    commit_all(repo)
    return repo


def propose(repo: Path, templates_dir: Path | None = None, **kw) -> detect.Proposal:
    """The proposal for `repo`, from the packaged table and, when given,
    `templates_dir`'s detectors.yaml."""
    return detect.propose(repo, detect.load(templates_dir), **kw)


def chosen(p: detect.Proposal, role: str, d: str = "") -> dict:
    """The candidate `p` proposed for `role` in directory `d`."""
    return next(c for c in p.candidates if c["chosen"] and c["role"] == role and c["dir"] == d)
