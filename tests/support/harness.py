from __future__ import annotations

import shutil
import subprocess
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


def isolated_bd(tmp_path: Path) -> Path:
    """A throwaway git repo with its own beads workspace. Return the repo path."""
    repo = tmp_path / "tracker"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    subprocess.run(
        ["bd", "init", "--prefix", "TEST"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
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
                }
            }
        )
    )
    return d


def e2e_templates_dir(tmp_path: Path) -> Path:
    return fake_templates_dir(tmp_path, "claude --model claude-haiku-4-5-20251001")
