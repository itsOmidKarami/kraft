from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from kraft.templates import Registry, load_registry

_SUPPORT = Path(__file__).parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


#: Commits are made at a fixed time so that building the same tree twice gives
#: the same SHA. A commit hash covers its own timestamp at one-second
#: granularity, so two `make_repo()` calls either side of a second boundary used
#: to produce different SHAs — a ~7% flake in any test comparing them, and worse
#: under load, because contention widens the gap between the two calls.
_FIXED_DATE = "2026-01-01T00:00:00+00:00"


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": _FIXED_DATE, "GIT_COMMITTER_DATE": _FIXED_DATE}
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env)


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


def isolated_bd(tmp_path: Path, name: str = "tracker") -> Path:
    """A throwaway git repo with its own beads workspace. Return the repo path.

    `name` distinguishes a second workspace in the same `tmp_path` — an
    auto-intaken bead lives in its own repo's `.beads`, not the instance-wide
    tracker (Kraft-8mu.5.2)."""
    repo = tmp_path / name
    shutil.copytree(_bd_template(), repo)
    return repo


def fake_registry(python_exe: str, fake_agent_path: Path) -> Registry:
    base = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    hooks = dict(base.hooks)
    fake = f"{python_exe} {fake_agent_path}"
    hooks["on.implementation.start"] = {"kind": "agent", "command": fake}
    # The shipped registry binds these to `claude`. A test that drives the
    # default chain must not shell out to the operator's real agent, and a
    # missing binary would land the item in needs_human rather than at a gate.
    for hook in ("on.spec.requested", "on.plan.requested", "on.chain.review_ready"):
        hooks[hook] = {**hooks[hook], "command": fake}
    # The shipped registry's back half is real now (Kraft-33j): four forge
    # hooks on `backend: auto`, and a human_review hook bound to the operator's
    # `claude`. A test driving the default chain must reach neither a forge CLI
    # nor a real agent, so they go back to noop here — the same reason the spec
    # and plan commands are swapped above.
    for hook in (
        "on.mr.open",
        "on.mr.sync",
        "on.ci.poll",
        "on.merge",
        "on.human_review.requested",
    ):
        hooks[hook] = {"kind": "builtin", "handler": "noop"}
    return Registry(hooks=hooks)


def fake_templates_dir(tmp_path: Path, agent_command: str, *, planning_hooks: bool = False) -> Path:
    """Registry + `quick-task`/`default` chains against a throwaway templates dir.

    `on.spec.requested`/`on.plan.requested` default to `builtin: noop` — most
    callers drive chains that never reach those gates and must not shell out.
    Pass `planning_hooks=True` to bind them to `agent_command` instead, the way
    `fake_registry` above does: spread the shipped binding and swap only the
    command, so its `skill:`/`artifact:` keys survive.

    `on.chain.review_ready` is always bound to `agent_command`, unlike spec and
    plan: `POST .../gates/chain_finalized/approve` reads and parses its
    artifact unconditionally now (Kraft-hm0), so a `noop` binding would leave
    every caller that approves that gate stuck at needs_human for a missing
    artifact. `fixtures/fake-claude.sh` answers with the chain's own unchanged
    tail, read back out of `chain_definition`, so a caller that never touches
    chain review still walks the rest of the chain exactly as before.
    """
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

    shipped = load_registry(_REPO_ROOT / "templates" / "registry.yaml").hooks
    if planning_hooks:
        spec_hook = {**shipped["on.spec.requested"], "command": agent_command}
        plan_hook = {**shipped["on.plan.requested"], "command": agent_command}
    else:
        spec_hook = noop()
        plan_hook = noop()

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
                    "on.spec.requested": spec_hook,
                    "on.plan.requested": plan_hook,
                    "on.chain.review_ready": {
                        **shipped["on.chain.review_ready"],
                        "command": agent_command,
                    },
                    "on.review.local.run": noop(),
                    "on.mr.open": noop(),
                    "on.ci.poll": noop(),
                    "on.review.mr.run": noop(),
                    "on.mr_checks.repair": noop(),
                    "on.mr.sync": noop(),
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
