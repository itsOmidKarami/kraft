from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from kraft.templates import Registry, load_registry

_SUPPORT = Path(__file__).parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def make_repo(tmp_path: Path, name: str = "sample") -> Path:
    dest = tmp_path / name
    shutil.copytree(_SUPPORT / "sample_repo", dest)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "t@t")
    _git(dest, "config", "user.name", "t")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-m", "init")
    return dest


def make_repo_with_engineering(tmp_path: Path, files: dict[str, str], name: str = "sample") -> Path:
    """make_repo(), then add repo-relative `files` (path -> text), commit, return the repo."""
    dest = make_repo(tmp_path, name)
    for rel, text in files.items():
        fp = dest / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-m", "add engineering docs")
    return dest


_bd_template_dir: Path | None = None


def _bd_template() -> Path:
    """One `bd init`ed workspace per test-run process, built lazily and reused.

    `bd init` spins up Dolt (~3.6s); doing it per test dominated the suite. Every
    caller only needs a *working* bd workspace (adapters run `bd create`/`close`,
    tests run `bd show <id>` — no test inspects cross-issue state), so we init
    once and hand out `copytree` copies.
    """
    global _bd_template_dir
    if _bd_template_dir is None:
        tpl = Path(tempfile.mkdtemp(prefix="kraft-bd-tpl-"))
        _git(tpl, "init", "-q", "-b", "main")
        _git(tpl, "config", "user.email", "t@t")
        _git(tpl, "config", "user.name", "t")
        subprocess.run(
            ["bd", "init", "--prefix", "TEST"],
            cwd=tpl,
            capture_output=True,
            text=True,
            check=True,
        )
        atexit.register(shutil.rmtree, tpl, ignore_errors=True)
        _bd_template_dir = tpl
    return _bd_template_dir


def isolated_bd(tmp_path: Path) -> Path:
    """A throwaway git repo with its own beads workspace. Return the repo path."""
    repo = tmp_path / "tracker"
    shutil.copytree(_bd_template(), repo)
    return repo


def fake_registry(python_exe: str, fake_agent_path: Path) -> Registry:
    base = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    hooks = dict(base.hooks)
    hooks["on.implementation.start"] = {
        "kind": "agent",
        "command": f"{python_exe} {fake_agent_path}",
    }
    return Registry(hooks=hooks)


def fake_templates_dir(tmp_path: Path, agent_command: str) -> Path:
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "quick-task.yaml", d / "quick-task.yaml")
    shutil.copy(_REPO_ROOT / "templates" / "default.yaml", d / "default.yaml")

    def noop() -> dict:
        # A fresh dict per call, not one shared object: `yaml.safe_dump` aliases
        # repeated *identical objects* with `&id001`/`*id001` anchors, which would
        # make a GET/PUT round trip through JSON (which de-aliases) an unrelated
        # byte diff rather than a real one.
        return {"kind": "builtin", "handler": "noop"}

    (d / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "hooks": {
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": agent_command},
                    "on.test.run": {
                        "kind": "subprocess",
                        "command": ["python", "-m", "pytest", "-q"],
                    },
                    "on.spec.requested": noop(),
                    "on.plan.requested": noop(),
                    "on.chain.review_ready": noop(),
                    "on.review.local.run": noop(),
                    "on.mr.open": noop(),
                    "on.ci.poll": noop(),
                    "on.review.mr.run": noop(),
                    "on.human_review.requested": noop(),
                    "on.merge": noop(),
                }
            }
        )
    )
    shutil.copy(_REPO_ROOT / "templates" / "policy.yaml", d / "policy.yaml")
    return d


def e2e_templates_dir(tmp_path: Path) -> Path:
    return fake_templates_dir(tmp_path, "claude --model claude-haiku-4-5-20251001")
