"""`kraft doctor`: the checks a human runs when Kraft is misbehaving.

One pass, one line per check, no `--fix` — doctor reports and the human decides.

Every check here answers a question someone actually asked once. That bar is the
only thing that stops a doctor command from growing output nobody reads
(spec E §4), so a new check belongs here after it has been wanted, not before.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from kraft import auth, client, config
from kraft.paths import RunDirs, default_run_dir, default_templates_dir

#: The agent CLI a chain launches. A fact about the shipped registry, not
#: configuration — `adapters/agent.py` has one profile and it is this one.
AGENT_COMMAND = "claude"


def _check(name: str, ok: bool, detail: str = "", *, skipped: bool = False) -> dict:
    """A skipped check is `ok`: it did not fail, it did not run. Only a real
    failure may set the exit code, or one dead server reads as five problems."""
    return {"name": name, "ok": ok, "detail": detail, "skipped": skipped}


async def run_checks() -> list[dict]:
    """Every check, in the order a human debugs: is it up, is it healthy, is it
    configured, can it launch an agent, is its state on disk still coherent."""
    checks: list[dict] = []
    try:
        health = await client.health()
        checks.append(_check("server", True, client.base_url()))
    except ValueError as exc:
        health = None
        checks.append(_check("server", False, str(exc)))

    checks.extend(
        _health_checks(health)
        if health is not None
        else [_check("health", True, "skipped: no server", skipped=True)]
    )
    checks.extend(_config_checks())
    checks.append(_agent_check())
    if health is None:
        checks.append(_check("repos", True, "skipped: no server", skipped=True))
        checks.append(_check("worktrees", True, "skipped: no server", skipped=True))
    else:
        checks.extend(await _repo_checks())
        checks.append(await _orphan_check())
    return checks


def _health_checks(payload: dict) -> list[dict]:
    """`/health`, one line per degraded reason.

    A single line reading "degraded" sends you to the browser anyway, which is
    the thing doctor exists to avoid.
    """
    index = payload.get("index") or {}
    embeddings = index.get("embeddings") or {}
    reasons = [
        f"invalid template {name}: {why}"
        for name, why in (payload.get("invalid_templates") or {}).items()
    ]
    if payload.get("invalid_policy"):
        reasons.append(f"invalid policy: {payload['invalid_policy']}")
    reasons.extend(f"index: {error}" for error in index.get("errors") or [])
    if not embeddings.get("available"):
        reasons.append(f"embeddings unavailable: {embeddings.get('reason') or 'no reason given'}")
    orphaned = (payload.get("reattach_summary") or {}).get("unknown") or []
    if orphaned:
        reasons.append(f"orphaned agent sessions: {', '.join(orphaned)}")
    if not reasons:
        return [_check("health", payload.get("status") == "ok", str(payload.get("status", "?")))]
    return [_check("health", False, reason) for reason in reasons]


def _config_checks() -> list[dict]:
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if not templates.is_dir():
        return [
            _check(
                "templates", False, f"{templates} does not exist — start `kraft` once to seed it"
            ),
            _token_check(),
        ]
    checks = [_check("templates", True, str(templates))]
    try:
        config.load_access(templates / "access.yaml")
        checks.append(_check("access.yaml", True, "parses"))
    except config.ConfigError as exc:
        checks.append(_check("access.yaml", False, str(exc)))
    checks.append(_token_check())
    return checks


def _token_check() -> dict:
    """The MCP bearer is a local credential: every other user on the machine can
    drive Kraft with a copy of it, so its mode is part of whether Kraft is well."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    path = run_dir / auth.MCP_TOKEN_FILE
    if not auth.read_mcp_token(run_dir):
        return _check(
            "mcp token", False, f"missing or empty at {path} — the server writes it on start"
        )
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        return _check("mcp token", False, f"{path}: {exc}")
    if mode & 0o077:
        return _check(
            "mcp token", False, f"{path} is {mode:04o}, not 0600 — any local user can read it"
        )
    return _check("mcp token", True, f"{path} ({mode:04o})")


def _agent_check() -> dict:
    """Passing loudly on the fake is the point: a dev instance that looks like it
    is working is spending no tokens on purpose, and that is worth reading once
    before you wonder why nothing real happened."""
    found = shutil.which(AGENT_COMMAND)
    if not found:
        return _check("agent cli", False, f"`{AGENT_COMMAND}` is not on PATH — no chain can run")
    real = os.path.realpath(found)
    if "fixtures" in Path(real).parts:
        return _check("agent cli", True, f"{found} -> {real} (the dev fake: it spends no tokens)")
    return _check("agent cli", True, found)


async def _repo_checks() -> list[dict]:
    """Through `GET /repos`, not `repos.yaml`: spec D §4 keeps one reader of the
    repo list on this side of the wire, and doctor is not an exception to it."""
    checks = []
    for repo in await client.repos():
        path = Path(repo["path"])
        name = f"repo {repo.get('name') or repo['path']}"
        if not path.is_dir():
            checks.append(_check(name, False, f"{path} no longer exists"))
        elif not (path / ".git").exists():
            # a file in a linked worktree, a directory in a normal clone
            checks.append(_check(name, False, f"{path} is no longer a git repo"))
        else:
            checks.append(_check(name, True, str(path)))
    return checks or [_check("repos", True, "none connected")]


async def _orphan_check() -> dict:
    """Worktrees with no work item row. Read-only, like every check here: which
    of them is safe to delete is a judgement about uncommitted work."""
    worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
    if not worktrees.is_dir():
        return _check("worktrees", True, f"{worktrees} does not exist yet")
    known = {item["id"] for item in await client.list_work_items()}
    try:
        orphans = sorted(d.name for d in worktrees.iterdir() if d.is_dir() and d.name not in known)
    except OSError as exc:
        return _check("worktrees", False, f"{worktrees}: {exc}")
    if orphans:
        return _check(
            "worktrees",
            False,
            f"{len(orphans)} under {worktrees} with no work item: {', '.join(orphans)}",
        )
    return _check("worktrees", True, str(worktrees))
