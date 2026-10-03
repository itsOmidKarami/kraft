"""`kraft repo connect --verify`: rehearse a repo's declared commands once, in
a throwaway worktree, while a person is still watching.

A work item's worktree is a fresh checkout of the commit the probe read
(`detect.source_ref`: origin's default branch, else HEAD) with nothing
untracked in it but the entry's `local_files`. So this runs exactly what a
work item would -- the entry's `setup_command` through a shell, then each
test scope's command split into argv, from the worktree's root, with the
worker's allowlisted environment and nothing more (no `CI=true`: workers do
not get one either, and a rehearsal that differs proves less) -- and reports
each one's outcome and time.

Two things a work item would trip over are checked on the way:

- a step that does not finish in `timeout` minutes, the usual cause being a
  test runner in watch mode, which a work item would wait on too;
- a step that leaves files git would commit (an unlocked `uv sync` writing
  uv.lock, a `build/` directory nobody ignored): every work item would commit
  them into its merge request.

On the host, always. A sandboxed repo's commands run in its sandbox when a
work item runs them, so rehearsing them on the host is refused unless the
caller says `on_host`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from kraft import builtins as builtins_mod
from kraft import config as config_mod
from kraft import detect
from kraft.worker.env import TEST_ENV, worker_env

#: Lines of a failed command's output shown; the rest is in the log it names.
_TAIL = 20
#: Fields the API tells beside an entry, which the entry itself does not have.
_TOLD = {
    "already_connected",
    "test_markers",
    "candidates",
    "scopes",
    "missing_setup",
    "read_from",
    "stopped",
}


def _run(
    argv: list[str] | str, cwd: Path, env: dict, log: Path, timeout_s: float
) -> tuple[str, float]:
    """("passed" | "failed" | "timed out", seconds). A step that times out,
    or is interrupted (Ctrl-C), is killed with every process it started:
    `sh -c` dying alone would leave its npm and node children writing into a
    worktree being removed. Its own session keeps the terminal's SIGINT from
    reaching it, so the kill is this function's to do."""
    started = time.monotonic()
    with log.open("w") as out:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                shell=isinstance(argv, str),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:  # a test command whose program is not installed
            out.write(f"{exc}\n")
            return "failed", time.monotonic() - started
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return "timed out", time.monotonic() - started
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
    return ("passed" if code == 0 else "failed"), time.monotonic() - started


def _tail(log: Path) -> str:
    lines = log.read_text("utf-8", "replace").splitlines()
    return "\n".join(f"    {line}" for line in lines[-_TAIL:])


def _changed(worktree: Path) -> tuple[set[str], set[str]]:
    """(what `git add -A` would stage, the installs left out of it). The
    first: untracked files git does not ignore, and tracked files changed,
    less Kraft's own roots and an untracked virtualenv, `node_modules` or
    `uv.lock` uv wrote, which Kraft's sweep leaves out
    (`forge.git.work_product_pathspec`). The second: those installs, by
    directory or file. The agent commits on its own too,
    and a `git add -A` of its takes one the repo does not ignore."""
    from kraft.adapters.forge.git import is_environment

    out = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    entries = [(entry[:2], entry[3:]) for entry in out.split("\0") if len(entry) > 3]
    kraft_roots = tuple(f"{r}/" for r in config_mod.KRAFT_ROOTS)

    def install(path: str) -> str | None:
        parts = path.split("/")
        for n in range(1, len(parts) + 1):
            if is_environment(worktree.joinpath(*parts[:n])):
                return "/".join(parts[:n])
        return None

    changed: set[str] = set()
    installs: set[str] = set()
    for code, p in entries:
        if p.startswith(kraft_roots):
            continue
        where = install(p) if code == "??" else None
        if where is None:
            changed.add(p)
        else:
            installs.add(where)
    return changed, installs


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


def _steps(entry: config_mod.RepoEntry, say) -> tuple[list[tuple[str, list[str] | str]], bool]:
    """The commands to run, in order, and whether the entry declares enough
    for a work item to start at all."""
    ok = True
    steps: list[tuple[str, list[str] | str]] = []
    if entry.setup_command is None:
        say("  setup: none declared -- a work item on this repo stops before it starts")
        ok = False
    elif entry.setup_command:
        steps.append(("setup", entry.setup_command))
    else:
        say('  setup: "" (nothing to prepare)')
    scopes = _scopes(entry)
    if not scopes:
        if entry.test_command == "":
            say('  tests: "" (this repo declares no tests)')
        else:
            say("  tests: none declared -- verify stops every work item here")
            ok = False
    ran_setups: set[str] = set()
    for label, area_setup, command in scopes:
        if area_setup and area_setup not in ran_setups:
            ran_setups.add(area_setup)
            steps.append((f"area setup ({label})", shlex.split(area_setup)))
        steps.append((f"test [{label}]", shlex.split(command)))
    return steps, ok


def verify(stored: dict, *, say=print, timeout_minutes: float = 30, on_host: bool = False) -> bool:
    """Run `stored`'s (a repos.yaml entry, as the API returns it) setup and
    test commands in a fresh worktree; True when every one passed and left
    nothing for a commit to pick up."""
    entry = config_mod.RepoEntry.model_validate(
        {k: v for k, v in stored.items() if k not in _TOLD},
        context={"unrecognised_keys_reported": True},
    )
    repo = Path(entry.path)
    if entry.effective_sandbox is not None and not on_host:
        say(
            "verify: this repo runs sandboxed, and --verify would run its commands on this "
            "machine instead; pass --on-host to do that anyway"
        )
        return False
    env = worker_env(entry)
    ref = detect.source_ref(repo) or "HEAD"
    logs = Path(tempfile.mkdtemp(prefix="kraft-verify-logs-"))
    worktree = Path(tempfile.mkdtemp(prefix="kraft-verify-")) / "checkout"
    added = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(worktree), ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        shutil.rmtree(worktree.parent, ignore_errors=True)
        shutil.rmtree(logs, ignore_errors=True)
        say(f"verify: could not cut a worktree of {ref}: {added.stderr.strip()}")
        return False
    say(f"verify: a fresh worktree of {ref} at {worktree}")
    try:
        ok = _rehearse(entry, repo, worktree, env, logs, timeout_minutes, say)
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(worktree.parent, ignore_errors=True)
    if ok:  # a failure's output is named above, for the person to read
        shutil.rmtree(logs, ignore_errors=True)
    say("verify: passed" if ok else "verify: failed -- fix the entry in repos.yaml and run again")
    return ok


def _rehearse(entry, repo, worktree, env, logs, timeout_minutes, say) -> bool:
    # The worker's own copy, with its guards: a file the repo does not
    # ignore, or one that would land outside the worktree, is not carried.
    _, refused = builtins_mod._carry_local_files(repo, worktree, list(entry.local_files))
    ok = not refused
    for rel in refused:
        say(f"  local_files: {rel} not carried: it is not a file, or the repo does not ignore it")
    baseline, installed = _changed(worktree)
    steps, declared = _steps(entry, say)
    ok = ok and declared
    for n, (label, argv) in enumerate(steps):
        shown = argv if isinstance(argv, str) else shlex.join(argv)
        say(f"  {label}: {shown} ...")
        log = logs / f"{n:02d}.log"
        # Dispatch runs an area's setup and a test with `TEST_ENV` on top, so
        # the bytecode a Python test would write is no file a work item leaves.
        step_env = env if label == "setup" else {**env, **TEST_ENV}
        outcome, took = _run(argv, worktree, step_env, log, timeout_minutes * 60)
        say(f"    {outcome if outcome == 'passed' else outcome.upper()} in {took:.1f}s")
        if outcome != "passed":
            ok = False
            say(_tail(log))
            say(f"    full output: {log}")
        if outcome == "timed out":
            say(
                f"    it did not finish in {timeout_minutes:g} minutes, and a work item would "
                "wait on it too. A test runner in watch mode is the usual cause: give it its "
                'run-once flag, or `env: {CI: "true"}` in the repo\'s repos.yaml entry, which '
                "workers get too"
            )
        changed, installs = _changed(worktree)
        left = sorted(changed - baseline)
        for where in sorted(installs - installed):
            # Not a failure: Kraft's own commits leave it out.
            if (worktree / where).is_file():
                say(
                    f"    left {where}, a lockfile the repo does not commit: Kraft's commits "
                    "leave it out, but an agent's `git add -A` would commit it. Commit one "
                    "(uv lock), so every work item installs the same versions"
                )
                continue
            say(
                f"    left {where}/, an install the repo does not ignore: Kraft's commits "
                "leave it out, but an agent's `git add -A` would commit it. Ignore it "
                "(.gitignore)"
            )
        installed |= installs
        if left:
            ok = False
            more = f" and {len(left) - 8} more" if len(left) > 8 else ""
            say(
                f"    left files every work item would commit: {', '.join(left[:8])}{more}. "
                "Ignore them (.gitignore), or have the command not write them (commit its "
                "lockfile)"
            )
            baseline |= set(left)
        if outcome != "passed" and label == "setup":
            say("  the tests were not run: nothing after a failed setup can be trusted")
            break
    return ok
