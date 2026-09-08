"""Opening a merge request, reading its CI, merging it.

One `Forge` shape, several backends, chosen by name in `registry.yaml`. A
direct-API backend is deliberately absent: `glab` and `gh` already hold their
credentials in the OS keyring, and a backend that talked to the REST API itself
would make Kraft responsible for a token — where it is read from, and that it
never reaches a log, an event payload, or a worker session's environment. That
is real work with no consumer until something runs without a CLI available
(Kraft-rki, Kraft-gzp).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

CIState = Literal["pending", "success", "failed"]

#: How long a `ci_poll` node waits for a pipeline to settle, and how long it
#: sleeps between checks. Both are overridable per node in the registry; a
#: pipeline slower than this needs a human whatever the number is.
DEFAULT_POLL_TIMEOUT = 1800.0
DEFAULT_POLL_INTERVAL = 5.0
_MAX_POLL_INTERVAL = 60.0


class ForgeError(RuntimeError):
    """The forge could not be reached, or answered something unusable."""


@dataclass(frozen=True)
class MR:
    number: int
    url: str


@dataclass(frozen=True)
class CIStatus:
    state: CIState
    url: str
    #: One line per job, for the human_review brief to render. A pipeline result
    #: with no detail leaves a reviewer nothing to act on.
    jobs: tuple[str, ...] = ()


class Forge(Protocol):
    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR: ...
    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None: ...
    async def ci_status(self, *, repo: Path, mr: MR, branch: str) -> CIStatus: ...
    async def merge(self, *, repo: Path, mr: MR) -> None: ...


@dataclass
class FakeForge:
    """In-memory forge for tests.

    `ci_states` is consumed one call at a time so a test can script
    pending-then-green without sleeping or polling; the last state repeats
    forever, so a test that only cares about the end state passes one.
    """

    ci_states: list[CIState] = field(default_factory=lambda: ["success"])
    opened: dict[int, str] = field(default_factory=dict)
    merged: list[int] = field(default_factory=list)
    #: Last description written per branch, so a test can see the sync land.
    bodies: dict[str, str] = field(default_factory=dict)

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        number = len(self.opened) + 1
        self.opened[number] = branch
        return MR(number=number, url=f"http://fake.forge/{number}")

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        self.bodies[branch] = body

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        state = self.ci_states.pop(0) if len(self.ci_states) > 1 else self.ci_states[0]
        return CIStatus(state=state, url=f"{mr.url}/pipelines", jobs=(f"fake-job: {state}",))

    async def merge(self, *, repo: Path, mr: MR) -> None:
        # number 0 means "resolve from the checked-out branch", which is what
        # `run_task` passes and what both real CLIs do. A fake that rejected it
        # would fail on the one path production always takes.
        number = mr.number if mr.number > 0 else max(self.opened, default=0)
        if number not in self.opened:
            raise ForgeError(f"no such merge request: {mr.number}")
        self.merged.append(number)


async def _run(repo: Path, args: list[str]) -> str:
    """One forge CLI call.

    FileNotFoundError becomes ForgeError so a binary that is missing, or a
    backend name that was never installed, reads as the configuration problem it
    is rather than as a traceback from three frames up.
    """
    try:
        done = await asyncio.to_thread(
            subprocess.run, args, cwd=repo, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise ForgeError(f"{args[0]} is not installed or not on PATH") from exc
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        raise ForgeError(f"{' '.join(args)} failed: {detail}")
    return done.stdout


async def _commits_on(repo: Path, branch: str) -> tuple[str, ...]:
    """Subjects of the commits this branch adds, newest last.

    `origin/main` and not the local `main`: a Kraft worktree is cut from
    whatever the local checkout happened to be at, which may be behind.
    Returns empty rather than raising — a description is not worth failing a
    node over.
    """
    try:
        raw = await _run(repo, ["git", "log", "--reverse", "--format=%s", f"origin/main..{branch}"])
    except ForgeError:
        return ()
    return tuple(line for line in raw.splitlines() if line.strip())


def mr_body(work_item_id: str, branch: str, commits: tuple[str, ...]) -> str:
    """The merge request description, rebuilt from the branch as it stands.

    Rebuilt and not appended: `open_mr` runs before verify and mr_checks add
    their commits, so a description written once describes a branch that no
    longer exists (Kraft-c09h).
    """
    lines = [f"Opened by Kraft for work item {work_item_id}.", ""]
    if commits:
        lines.append("Commits on this branch:")
        lines.append("")
        lines += [f"- {c}" for c in commits]
        lines.append("")
    lines.append(f"Branch `{branch}`. Review the diff and the pipeline before merging.")
    return "\n".join(lines)


#: A forge title is a headline; a Kraft work item title is a paragraph (the
#: batch that fixed this bug had a 360-character one). First line, clipped.
MR_TITLE_MAX = 72


def mr_title(title: str) -> str:
    """The work item title, cut down to something a merge request can wear."""
    head = title.strip().splitlines()[0].strip() if title.strip() else ""
    if not head:
        return "Kraft work item"
    return head if len(head) <= MR_TITLE_MAX else head[: MR_TITLE_MAX - 1].rstrip() + "…"


#: glab's pipeline vocabulary, from `glab ci list --help` (glab 1.116.0).
#: 'skipped' is deliberately not success: nothing proved the branch green, and
#: the next node is merge. Anything unrecognised falls through to 'failed' for
#: the same reason — guessing in the direction of merging is the one guess that
#: cannot be walked back.
_GLAB_STATES: dict[str, CIState] = {
    "success": "success",
    "failed": "failed",
    "canceled": "failed",
    "skipped": "failed",
    "running": "pending",
    "pending": "pending",
    "created": "pending",
    "preparing": "pending",
    "waiting_for_resource": "pending",
    "scheduled": "pending",
    "manual": "pending",
}


class GlabCli:
    """GitLab through `glab`. Credentials stay in glab's own keyring."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        # Both forges refuse to create against an unpushed branch. `--fill --yes`
        # would push too, but pushing explicitly keeps the failure legible when
        # it is the push that fails rather than the create.
        await _run(repo, ["git", "push", "-u", "origin", branch])
        # Not `--fill`: it derives the title from the commits, and with more
        # than one commit glab falls back to the branch name — which for Kraft
        # is always the work item id, so every MR read as a hex string
        # (Kraft-c09h).
        await _run(
            repo,
            ["glab", "mr", "create", "--title", mr_title(title), "--description", body, "--yes"],
        )
        # Read the MR back rather than parsing create's human-formatted output.
        raw = await _run(repo, ["glab", "mr", "view", "-F", "json"])
        data = _parse_json(raw, "glab mr view")
        return MR(number=int(data["iid"]), url=str(data["web_url"]))

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        # No iid: `glab mr update` resolves the merge request from the
        # checked-out branch, the way merge and ci already do.
        await _run(repo, ["glab", "mr", "update", "--description", body])

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # --ref, or this returns the newest pipeline in the whole project: a
        # green run on main would pass the gate for a red branch.
        ref = ["--ref", branch] if branch else []
        raw = await _run(repo, ["glab", "ci", "list", "-F", "json", "-P", "1", *ref])
        rows = _parse_json(raw, "glab ci list")
        if not rows:
            # No pipeline yet is not a green one.
            return CIStatus(state="pending", url="", jobs=("no pipeline yet",))
        top = rows[0]
        raw_state = str(top.get("status", ""))
        return CIStatus(
            state=_GLAB_STATES.get(raw_state, "failed"),
            url=str(top.get("web_url", "")),
            jobs=(f"pipeline {top.get('id')}: {raw_state}",),
        )

    async def merge(self, *, repo: Path, mr: MR) -> None:
        # number 0 is "not known": `run_task` does not thread the MR between
        # nodes, and both CLIs resolve it from the checked-out branch. Passing
        # a literal 0 would target a merge request that does not exist.
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["glab", "mr", "merge", *target, "--yes"])


class GhCli:
    """GitHub through `gh`. For the public repo after the v0.1.0 split."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        await _run(repo, ["git", "push", "-u", "origin", branch])
        # `--fill` titles the PR from the commits; see GlabCli.open_mr.
        await _run(repo, ["gh", "pr", "create", "--title", mr_title(title), "--body", body])
        raw = await _run(repo, ["gh", "pr", "view", "--json", "number,url"])
        data = _parse_json(raw, "gh pr view")
        return MR(number=int(data["number"]), url=str(data["url"]))

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        await _run(repo, ["gh", "pr", "edit", "--body", body])

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # `gh pr view` with no argument already resolves from the current
        # branch, so `branch` is accepted for one Forge shape and unused here.
        raw = await _run(repo, ["gh", "pr", "view", "--json", "number,url,statusCheckRollup"])
        data = _parse_json(raw, "gh pr view")
        checks = data.get("statusCheckRollup") or []
        if not checks:
            return CIStatus(state="pending", url=str(data.get("url", "")), jobs=("no checks yet",))
        jobs = tuple(f"{c.get('name')}: {c.get('conclusion') or 'PENDING'}" for c in checks)
        conclusions = [str(c.get("conclusion") or "") for c in checks]
        if any(c in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for c in conclusions):
            state: CIState = "failed"
        elif all(c in ("SUCCESS", "NEUTRAL") for c in conclusions):
            state = "success"
        else:
            state = "pending"
        return CIStatus(state=state, url=str(data.get("url", "")), jobs=jobs)

    async def merge(self, *, repo: Path, mr: MR) -> None:
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["gh", "pr", "merge", *target, "--squash"])


def _parse_json(raw: str, what: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ForgeError(f"{what} did not return JSON: {raw[:200]!r}") from exc


def resolve(name: str) -> Forge:
    """Named, never probed.

    Auto-detecting an available CLI would mean the same work item takes a
    different path on a laptop than in a container, and a bug that reproduces on
    one and not the other. A `glab` that is installed but unauthenticated also
    looks available and then fails deep inside a node.

    The accepted names are duplicated in `templates._FORGE_BACKENDS`, which
    validates a registry file without importing this module. Edit both together.
    """
    match name:
        case "glab":
            return GlabCli()
        case "gh":
            return GhCli()
        case "fake":
            return FakeForge()
        case _:
            raise ForgeError(f"unknown forge backend {name!r}; known: gh, glab, fake")


async def _poll_ci(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> tuple[CIStatus, bool]:
    """Wait for a pipeline to settle.

    Returns the last status seen and whether the wait ran out with it still
    pending. Backoff rather than a fixed interval: a thirty-minute pipeline
    should not cost three hundred CLI invocations, and the early checks are the
    ones worth making promptly.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    # The cap bounds how far the backoff *grows*, not what the caller asked
    # for: a registry that sets a 300s interval for a rate-limited forge must
    # not silently get 60s and five times the CLI calls.
    cap = max(_MAX_POLL_INTERVAL, interval)
    while True:
        ci = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
        if ci.state != "pending":
            return ci, False
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ci, True
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * 2, cap)


async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    handler: str,
    backend: str,
    repo: Path,
    branch: str,
    title: str,
    round: int = 0,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> str:
    """One forge node.

    Records a session the way a builtin does rather than the way the subprocess
    adapter does: the work happens in this process, so there is no child to
    supervise, no log fd to hand over and nothing for reattach to adopt.
    """
    from kraft import builtins as _builtins

    body = mr_body(work_item_id, branch, await _commits_on(repo, branch))
    forge = resolve(backend)
    try:
        match handler:
            case "open_mr":
                mr = await forge.open_mr(repo=repo, branch=branch, title=title, body=body)
                log, status = f"opened {mr.url}\n", "done"
            case "ci_poll":
                # Both CLIs resolve the merge request from the checked-out
                # branch, so the number is not threaded between nodes.
                ci, timed_out = await _poll_ci(
                    forge,
                    repo=repo,
                    branch=branch,
                    timeout=poll_timeout,
                    interval=poll_interval,
                )
                # A timeout and a red pipeline are both a failed node, but a
                # reviewer -- and any fix loop built on this node (Kraft-cbr) --
                # has to tell "finished red" from "never finished".
                head = (
                    f"pipeline timed out after {poll_timeout:g}s, still pending"
                    if timed_out
                    else f"pipeline {ci.state}"
                )
                log = f"{head}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
                status = "done" if ci.state == "success" else "failed"
            case "sync_mr":
                # The description `open_mr` wrote predates every commit verify
                # and mr_checks added, so it is rewritten from the branch head
                # before a human is asked to read it (Kraft-c09h).
                await forge.update_mr(repo=repo, branch=branch, body=body)
                log, status = "merge request description synced\n", "done"
            case "merge":
                await forge.merge(repo=repo, mr=MR(number=0, url=""))
                log, status = "merged\n", "done"
            case _:
                log, status = f"unknown forge handler {handler!r}\n", "failed"
    except ForgeError as exc:
        log, status = f"{hook_point} failed: {exc}\n", "failed"

    return await _builtins._record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        log=log,
        status=status,
    )
