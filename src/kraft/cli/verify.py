"""`kraft repo connect --verify`: rehearse a repo's declared commands once, in
a throwaway worktree, while a person is still watching.

A worktree is what every work item gets: a fresh checkout of HEAD with
nothing untracked in it but the entry's `local_files`. So this runs exactly
what a work item would -- the entry's `setup_command` through a shell, then
each test scope's command split into argv, from the worktree's root, with the
worker's allowlisted environment -- and reports each one's outcome and time.

On this machine and on the host, always: a sandboxed repo's commands run in
its sandbox when a work item runs them, so a pass here proves less for it,
and the report says so.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from kraft import config as config_mod
from kraft.worker.env import worker_env

#: Lines of a failed command's output shown; the rest is in the log it names.
_TAIL = 20


def _run(argv: list[str] | str, cwd: Path, env: dict, log: Path) -> tuple[bool, float]:
    started = time.monotonic()
    with log.open("w") as out:
        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                shell=isinstance(argv, str),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            ok = done.returncode == 0
        except OSError as exc:  # a test command whose program is not installed
            out.write(f"{exc}\n")
            ok = False
    return ok, time.monotonic() - started


def _tail(log: Path) -> str:
    lines = log.read_text("utf-8", "replace").splitlines()
    return "\n".join(f"    {line}" for line in lines[-_TAIL:])


def _scopes(entry: config_mod.RepoEntry) -> list[tuple[str, str | None, str]]:
    """(label, setup to run first, command) per test scope, the repository's
    then each area's -- `dispatch._select_scopes`' table, with every scope
    selected, as a change touching everything would."""
    scopes = [(", ".join(s.paths), None, s.command) for s in entry.test_scopes or ()]
    if not scopes and entry.test_command:
        scopes = [("**", None, entry.test_command)]
    for name, area in entry.areas.items():
        for s in (area.get("verification") or {}).get("test_scopes") or []:
            scopes.append(
                (f"area {name}: {', '.join(s['paths'])}", area.get("setup"), s["command"])
            )
    return scopes


def verify(stored: dict, *, say=print) -> bool:
    """Run `stored`'s (a repos.yaml entry, as the API returns it) setup and
    test commands in a fresh worktree; True when every one passed."""
    told = {"already_connected", "test_markers", "candidates", "scopes", "missing_setup"}
    entry = config_mod.RepoEntry.model_validate(
        {k: v for k, v in stored.items() if k not in told},
        context={"unrecognised_keys_reported": True},
    )
    repo = Path(entry.path)
    env = worker_env(entry)
    if entry.effective_sandbox is not None:
        say("note: this repo runs sandboxed; --verify runs its commands on this machine instead")
    logs = Path(tempfile.mkdtemp(prefix="kraft-verify-logs-"))
    worktree = Path(tempfile.mkdtemp(prefix="kraft-verify-")) / "checkout"
    added = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(worktree), "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        say(f"verify: could not cut a worktree: {added.stderr.strip()}")
        return False
    say(f"verify: a fresh worktree of HEAD at {worktree}")
    ok = True
    try:
        for rel in entry.local_files:
            if (repo / rel).is_file():
                (worktree / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo / rel, worktree / rel)
        steps: list[tuple[str, list[str] | str]] = []
        if entry.setup_command is None:
            say("  setup: none declared -- a work item on this repo stops before it starts")
            ok = False
        elif entry.setup_command:
            steps.append(("setup", entry.setup_command))
        else:
            say('  setup: "" (nothing to prepare)')
        ran_setups: set[str] = set()
        scopes = _scopes(entry)
        if not scopes:
            if entry.test_command == "":
                say('  tests: "" (this repo declares no tests)')
            else:
                say("  tests: none declared -- verify stops every work item here")
                ok = False
        for label, area_setup, command in scopes:
            if area_setup and area_setup not in ran_setups:
                ran_setups.add(area_setup)
                steps.append((f"area setup ({label})", shlex.split(area_setup)))
            steps.append((f"test [{label}]", shlex.split(command)))
        for n, (label, argv) in enumerate(steps):
            shown = argv if isinstance(argv, str) else shlex.join(argv)
            say(f"  {label}: {shown} ...")
            log = logs / f"{n:02d}.log"
            passed, took = _run(argv, worktree, env, log)
            say(f"    {'passed' if passed else 'FAILED'} in {took:.1f}s")
            if not passed:
                ok = False
                say(_tail(log))
                say(f"    full output: {log}")
                if label == "setup":
                    say("  the tests were not run: nothing after a failed setup can be trusted")
                    break
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(worktree.parent, ignore_errors=True)
    say("verify: passed" if ok else "verify: failed -- fix the entry in repos.yaml and run again")
    return ok
