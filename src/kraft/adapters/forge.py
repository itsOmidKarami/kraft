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

from kraft import events

CIState = Literal["pending", "success", "failed"]

#: How long a `ci_poll` node waits for a pipeline to settle, and how long it
#: sleeps between checks. Both are overridable per node in the registry; a
#: pipeline slower than this needs a human whatever the number is.
DEFAULT_POLL_TIMEOUT = 1800.0
DEFAULT_POLL_INTERVAL = 5.0
_MAX_POLL_INTERVAL = 60.0

#: How long the merge node waits for the forge to report the merge request
#: actually merged, and how long it sleeps between reads. Five minutes rather
#: than DEFAULT_POLL_TIMEOUT's thirty: the pipeline was already green at
#: mr_checks, so a merge that has not landed by now is waiting on something a
#: person has to see. Deliberately not registry-tunable — nothing has asked,
#: and these are one edit away if something does.
MERGE_VERIFY_TIMEOUT = 300.0
MERGE_VERIFY_INTERVAL = 5.0


class ForgeError(RuntimeError):
    """The forge could not be reached, or answered something unusable."""


@dataclass(frozen=True)
class MR:
    number: int
    url: str


@dataclass(frozen=True)
class MRRef:
    """A merge request the forge already has for a branch, and its state.

    Distinct from `MR`, which is what `open_mr` just created and what `merge`
    and `ci_status` are handed: neither has any use for a state that was true
    one CLI call ago.
    """

    number: int
    url: str
    state: Literal["open", "merged", "closed"]


@dataclass(frozen=True)
class CIStatus:
    state: CIState
    url: str
    #: One line per job, for the human_review brief to render. A pipeline result
    #: with no detail leaves a reviewer nothing to act on.
    jobs: tuple[str, ...] = ()
    #: Whether the *merge request* can land, which is a different question from
    #: whether its pipeline is green: a branch that conflicts with main is green
    #: right up to the merge that fails (Kraft-ejj9). None means the forge has
    #: not decided, or does not say — see `_mergeable`. Defaulted so FakeForge
    #: and every existing caller are unaffected.
    mergeable: bool | None = None
    #: The raw state the forge gave, for the log line a human reads.
    merge_detail: str = ""


class Forge(Protocol):
    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR: ...
    async def push(self, *, repo: Path, branch: str) -> None: ...
    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None: ...
    async def ci_status(self, *, repo: Path, mr: MR, branch: str) -> CIStatus: ...
    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None: ...
    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None: ...
    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None: ...


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
    #: Branches pushed, in call order, so a test can see the push land without
    #: a network or a git remote.
    pushed: list[str] = field(default_factory=list)
    #: What `ci_status` says about the merge request itself, as distinct from
    #: its pipeline. None (undecided) is the default, so tests that predate
    #: this are unaffected.
    mergeable: bool | None = None
    merge_detail: str = ""
    #: Labels `set_labels` put on the merge request, in call order.
    labels: list[str] = field(default_factory=list)
    #: repo each `opened` number belongs to (Kraft-qlsf). The real CLIs
    #: disambiguate two repos sharing a branch name by their own cwd; a fake
    #: keyed on branch alone would let a submodule's `find_mr` "discover" the
    #: root's merge request just because both are on `kraft/<item>`.
    _opened_repo: dict[int, str] = field(default_factory=dict)

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        number = len(self.opened) + 1
        self.opened[number] = branch
        self._opened_repo[number] = str(repo)
        return MR(number=number, url=f"http://fake.forge/{number}")

    async def push(self, *, repo: Path, branch: str) -> None:
        self.pushed.append(branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        self.bodies[branch] = body

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        state = self.ci_states.pop(0) if len(self.ci_states) > 1 else self.ci_states[0]
        return CIStatus(
            state=state,
            url=f"{mr.url}/pipelines",
            jobs=(f"fake-job: {state}",),
            mergeable=self.mergeable,
            merge_detail=self.merge_detail,
        )

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        self.labels.extend(labels)

    async def merge(self, *, repo: Path, branch: str = "", mr: MR) -> None:
        # number 0 means "resolve from the checked-out branch", which is what
        # `run_task` passes and what both real CLIs do. A fake that rejected it
        # would fail on the one path production always takes.
        if mr.number > 0:
            number = mr.number
        else:
            candidates = [
                n for n, b in self.opened.items() if b == branch and self._matches(n, repo)
            ]
            number = max(candidates, default=0)
        if number not in self.opened:
            raise ForgeError(f"no such merge request: {mr.number}")
        self.merged.append(number)

    def _matches(self, number: int, repo: Path) -> bool:
        """A number opened before `_opened_repo` existed (an older test's
        opened={...} literal) matches any repo -- back-compatible, not
        repo-aware."""
        recorded = self._opened_repo.get(number)
        return recorded is None or recorded == str(repo)

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        numbers = [n for n, b in self.opened.items() if b == branch and self._matches(n, repo)]
        if not numbers:
            return None
        number = next((n for n in numbers if n not in self.merged), numbers[0])
        return MRRef(
            number=number,
            url=f"http://fake.forge/{number}",
            state="merged" if number in self.merged else "open",
        )


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


async def _kraft_written_paths(repo: Path) -> list[str]:
    """Untracked paths under `.engineering/` -- Kraft's own artifacts and
    session notes, which hooks write straight to disk and never `git add`.

    Deliberately not a static pathspec: a path this repo already tracked
    under `.engineering/` before this worktree existed shows as modified
    ('M'), not untracked ('??'), so it is never in this list. An edit to that
    file is the repo's own content and real work product, not Kraft's
    bookkeeping -- excluding it outright, the way a blanket
    `:(exclude).engineering` pathspec used to, silently dropped it from every
    merge request (caught in review: a worker's edit to a pre-existing,
    already-committed `.engineering/specs/x.md` would otherwise vanish).
    """
    raw = await _run(repo, ["git", "status", "--porcelain", "--", ".engineering"])
    return [line[3:] for line in raw.splitlines() if line.startswith("??")]


async def _work_product_pathspec(repo: Path) -> list[str]:
    """`.`, plus an exclusion for every path Kraft itself wrote into this
    worktree's `.engineering/` -- session summaries, and now
    spec/plan/chain_review/review_brief too. None of it is the agent's work
    product, so neither the clean check nor the straggler sweep may treat it
    as such: it is ingested straight into the index at gate approval instead
    (`Indexer.ingest_gate_artifact`), and never lands in the connected repo's
    git history at all.

    Computed per call rather than a fixed list: which paths are Kraft's own
    depends on what this repo already tracked before Kraft touched it
    (`_kraft_written_paths`), which no static pathspec can know.

    Individual paths rather than a `.gitignore` line or a blanket
    `:(exclude).engineering`: this repo ignores `.engineering/` for exactly
    this reason (.gitignore:54), but a repo Kraft was pointed at five minutes
    ago does not, and Kraft must not put its own bookkeeping into that repo's
    first merge request — or refuse to open one over it (Kraft-z8gj, widened:
    the same argument that carved out session summaries applies to
    spec/plan/chain_review/review_brief once none of them are committed
    either) — without also assuming every path under `.engineering/` is ours.
    """
    return [".", *(f":(exclude){p}" for p in await _kraft_written_paths(repo))]


async def _assert_clean(repo: Path) -> None:
    """Refuse to open a merge request over a worktree with uncommitted work.

    Untracked files are included on purpose: a source or test file the agent
    never `git add`ed is what went missing on work item 5163dd1b. Ignored files
    are excluded by git itself, so `.pytest_cache/` does not trip it, and
    `_work_product_pathspec` drops Kraft's own session notes and artifacts in
    a repo that has not ignored them.

    `--ignore-submodules=none` deliberately overrides the repo's own
    `submodule.<path>.ignore` config. A human setting `ignore = all` on a
    workspace with several submodules to stop pointer churn in every `git
    status` is reasonable; it is not permission for Kraft to open a merge
    request over a submodule holding commits that request will not carry
    (Kraft-qlsf — this is what let work item 9d0ab38ff3c9439b90506df0f6966660
    push a submodule commit nowhere while every guard reported clean).
    """
    pathspec = await _work_product_pathspec(repo)
    raw = await _run(
        repo, ["git", "status", "--porcelain", "--ignore-submodules=none", "--", *pathspec]
    )
    dirty = [line[3:] for line in raw.splitlines() if line.strip()]
    if dirty:
        shown = ", ".join(dirty[:5])
        more = f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else ""
        raise ForgeError(
            f"{len(dirty)} uncommitted path(s) in the worktree, which would not "
            f"reach the merge request: {shown}{more}"
        )


async def commit_stragglers(repo: Path, *, message: str) -> bool:
    """Commit whatever an agent left behind in the worktree. True if it did.

    A worker is told to commit everything it changes before it exits, and one
    that does not leaves work `_assert_clean` refuses two nodes later and a
    worktree prune destroys — with `verify` passing in between, because the
    files are on disk (Kraft-7fip). Kraft owns the worktree and the branch is
    throwaway, so there is nothing to protect by refusing: commit it, and let
    the merge request carry it.

    Ignored files stay out, the same exclusion `_assert_clean` relies on, so a
    `.pytest_cache/` left behind is not mistaken for work — and
    `_work_product_pathspec` keeps Kraft's own session notes and artifacts out
    of the merge request even in a repo that has never heard of them.
    """
    pathspec = await _work_product_pathspec(repo)
    if not (await _run(repo, ["git", "status", "--porcelain", "--", *pathspec])).strip():
        return False
    await _run(repo, ["git", "add", "-A", "--", *pathspec])
    try:
        await _run(repo, ["git", "commit", "-m", message])
    except ForgeError:
        # A commit hook that reformats what it is given exits non-zero with the
        # files rewritten under it. Re-adding takes its edits; --no-verify then
        # refuses to let a second opinion cost us the work, which is the whole
        # point of this function. A hook that fails for any other reason loses
        # nothing either -- the commit is what keeps the work reachable.
        await _run(repo, ["git", "add", "-A", "--", *pathspec])
        await _run(repo, ["git", "commit", "--no-verify", "-m", message])
    return True


async def _assert_pushed(repo: Path, branch: str) -> None:
    """Refuse to merge a head the remote has never seen.

    If `origin/<branch>` does not exist, `_run` raises ForgeError of its own and
    the node fails loudly — the right answer for a merge with no pushed branch.
    """
    raw = await _run(repo, ["git", "rev-list", "--count", f"origin/{branch}..HEAD"])
    ahead = int(raw.strip() or 0)
    if ahead:
        raise ForgeError(
            f"local branch is ahead of origin/{branch} by {ahead} commit(s); "
            "merging would merge a head the forge has never seen"
        )


async def _head_sha(repo: Path) -> str:
    """The worktree's HEAD, or empty when git will not say.

    Empty means "do not compare": a repo git cannot read is no reason to
    report a pipeline that was read perfectly well as missing.
    """
    try:
        return (await _run(repo, ["git", "rev-parse", "HEAD"])).strip()
    except ForgeError:
        return ""


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


#: glab's merge request vocabulary (`glab mr list -F json`, glab 1.117.0).
#: 'locked' is an open merge request with discussion locked, so it is open
#: here; anything unrecognised is 'closed', which fails the merge node loudly
#: rather than guessing in the one direction that cannot be walked back.
_GLAB_MR_STATES: dict[str, str] = {
    "opened": "open",
    "locked": "open",
    "merged": "merged",
    "closed": "closed",
}
_GH_MR_STATES: dict[str, str] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}


#: Merge-request states that need a *code* change before anything can land:
#: glab's `detailed_merge_status`/`merge_status`, gh's `mergeable` and
#: `mergeStateStatus`.
_UNMERGEABLE = {
    "conflict",
    "need_rebase",
    "broken_status",
    "cannot_be_merged",
    "CONFLICTING",
    "DIRTY",
}
_MERGEABLE = {"mergeable", "can_be_merged", "MERGEABLE"}


def _mergeable(*states: str) -> bool | None:
    """Can this merge request land, as far as the forge will say.

    Deliberately the opposite convention to `_GLAB_STATES`, where unknown is
    failure: `mr_checks` runs *before* the human_review gate, so `not_approved`,
    `ci_still_running`, `discussions_not_resolved`, `draft_status`, `checking`,
    `unchecked` and `UNKNOWN` are the ordinary states of a healthy merge request
    at this node, and mapping them to failure would fail the node on every repo
    with an approval rule. Only states that need a code change fail here. States
    that need a *person* are the gate's business, and the merge node reads the
    merge back rather than letting one through silently (Kraft-79x3).

    Variadic because GitHub answers in two fields and either can carry the bad
    news.
    """
    if any(s in _UNMERGEABLE for s in states):
        return False
    if any(s in _MERGEABLE for s in states):
        return True
    return None


def _pick_mr(rows: list[MRRef]) -> MRRef | None:
    """The open merge request for a branch, else the first one the forge listed."""
    return next((r for r in rows if r.state == "open"), rows[0] if rows else None)


class GlabCli:
    """GitLab through `glab`. Credentials stay in glab's own keyring."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        await _assert_clean(repo)
        # Both forges refuse to create against an unpushed branch. `--fill --yes`
        # would push too, but pushing explicitly keeps the failure legible when
        # it is the push that fails rather than the create.
        await self.push(repo=repo, branch=branch)
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

    async def push(self, *, repo: Path, branch: str) -> None:
        await _run(repo, ["git", "push", "-u", "origin", branch])

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        # No iid: `glab mr update` resolves the merge request from the
        # checked-out branch, the way merge and ci already do.
        await _run(repo, ["glab", "mr", "update", "--description", body])

    async def _merge_state(self, repo: Path) -> str:
        """The merge request's own view of whether it can merge.

        `glab mr view -F json` is the call `open_mr` already makes, resolved
        from the checked-out branch. `detailed_merge_status` says *why*;
        `merge_status` is the older, coarser field, kept as a fallback for an
        older GitLab. Anything else — including a payload that is not an object
        — is "the forge did not say", which `_mergeable` reads as undecided.
        """
        data = _parse_json(await _run(repo, ["glab", "mr", "view", "-F", "json"]), "glab mr view")
        if not isinstance(data, dict):
            return ""
        return str(data.get("detailed_merge_status") or data.get("merge_status") or "")

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # The merge request's own state first: a conflict fails the check node
        # whatever colour the pipeline is, and `_poll_ci` must not wait out a
        # pipeline to learn it (Kraft-ejj9).
        detail = await self._merge_state(repo)
        mergeable = _mergeable(detail)
        # --ref, or this returns the newest pipeline in the whole project: a
        # green run on main would pass the gate for a red branch.
        ref = ["--ref", branch] if branch else []
        raw = await _run(repo, ["glab", "ci", "list", "-F", "json", "-P", "1", *ref])
        rows = _parse_json(raw, "glab ci list")
        # No pipeline yet is not a green one.
        state: CIState = "pending"
        url, jobs = "", ("no pipeline yet",)
        if rows:
            top = rows[0]
            # For a few seconds after a push, this list still answers with the
            # *previous* commit's pipeline — green, for code the branch no
            # longer has. ci_poll pushes now (Kraft-bxj8), so that window is on
            # the hot path, and a pipeline that is not for this head is not a
            # result. Same answer as no pipeline at all: pending.
            sha = str(top.get("sha", ""))
            head = await _head_sha(repo) if sha else ""
            if sha and head and sha != head:
                jobs = (f"no pipeline for {head[:7]} yet",)
            else:
                raw_state = str(top.get("status", ""))
                state = _GLAB_STATES.get(raw_state, "failed")
                url = str(top.get("web_url", ""))
                jobs = (f"pipeline {top.get('id')}: {raw_state}",)
                if state == "failed":
                    jobs += await self._failure_detail(repo, top.get("id"))
        return CIStatus(state=state, url=url, jobs=jobs, mergeable=mergeable, merge_detail=detail)

    async def _failure_detail(self, repo: Path, pipeline_id) -> tuple[str, ...]:
        """The failed jobs of a red pipeline, and the tail of what each printed.

        "pipeline 2832908143: failed" is the whole of what this node used to
        report, which is not enough for a human to act on and not enough for
        anything to remediate: MR !89 was red because a job wanted a
        `release::` label, and that sentence existed only in the job's trace
        (Kraft-xh0q).

        Best effort throughout. A diagnosis that cannot be fetched must not
        turn a pipeline result that *was* read into a node failure -- the
        pipeline is red either way, and that is the answer the caller needs.
        """
        if not pipeline_id:
            return ()
        pid = str(pipeline_id)
        try:
            raw = await _run(repo, ["glab", "ci", "get", "--pipeline-id", pid, "-F", "json"])
            data = _parse_json(raw, "glab ci get")
        except ForgeError:
            return ()
        if not isinstance(data, dict):
            return ()
        failed = [
            str(j.get("name", ""))
            for j in (data.get("jobs") or [])
            if isinstance(j, dict) and str(j.get("status", "")) == "failed"
        ]
        detail: tuple[str, ...] = ()
        # ponytail: first two failed jobs, last 15 lines each. A pipeline where
        # everything failed is one story, told twice over; the cap keeps this
        # out of the review brief's way. Widen it if a real pipeline needs it.
        for name in failed[:2]:
            detail += (f"job {name}: failed",)
            try:
                trace = await _run(repo, ["glab", "ci", "trace", name, "--pipeline-id", pid])
            except ForgeError:
                continue
            detail += tuple(f"  {line}" for line in trace.strip().splitlines()[-15:])
        return detail

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the merge request, then start a pipeline that can see it.

        `CI_MERGE_REQUEST_LABELS` is fixed when a pipeline is created, so a job
        that failed for a missing label re-reads the old, empty value on retry.
        Labelling alone therefore leaves the merge request looking fixed and
        still red -- the re-create is part of the capability, not the caller's
        homework (Kraft-xh0q).
        """
        if not labels:
            return
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["glab", "mr", "update", *target, "--label", ",".join(labels)])
        # `run_task` passes number 0 -- "resolve it from the checked-out branch"
        # -- to every forge handler, so that is the number this gets on the path
        # production actually takes. Skipping the re-create for it would leave
        # the one real caller with a labelled merge request and the same red
        # pipeline, which is exactly what the re-create exists to prevent.
        number = mr.number
        if number <= 0:
            raw = await _run(repo, ["glab", "mr", "view", "-F", "json"])
            number = int(_parse_json(raw, "glab mr view")["iid"])
        # `projects/:id` is glab's own placeholder for the repo the command is
        # run in, so this stays as repo-agnostic as every other call here.
        await _run(
            repo,
            ["glab", "api", "-X", "POST", f"projects/:id/merge_requests/{number}/pipelines"],
        )

    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None:
        await _assert_pushed(repo, branch)
        # number 0 is "not known": `run_task` does not thread the MR between
        # nodes, and both CLIs resolve it from the checked-out branch. Passing
        # a literal 0 would target a merge request that does not exist.
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["glab", "mr", "merge", *target, "--yes"])

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        # --all, or a merged merge request reads as no merge request at all.
        raw = await _run(
            repo,
            ["glab", "mr", "list", "--all", "--source-branch", branch, "-F", "json", "-P", "5"],
        )
        rows = _parse_json(raw, "glab mr list")
        return _pick_mr(
            [
                MRRef(
                    number=int(r["iid"]),
                    url=str(r.get("web_url", "")),
                    state=_GLAB_MR_STATES.get(str(r.get("state", "")), "closed"),
                )
                for r in rows
            ]
        )


class GhCli:
    """GitHub through `gh`. For the public repo after the v0.1.0 split."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        await _assert_clean(repo)
        await self.push(repo=repo, branch=branch)
        # `--fill` titles the PR from the commits; see GlabCli.open_mr.
        await _run(repo, ["gh", "pr", "create", "--title", mr_title(title), "--body", body])
        raw = await _run(repo, ["gh", "pr", "view", "--json", "number,url"])
        data = _parse_json(raw, "gh pr view")
        return MR(number=int(data["number"]), url=str(data["url"]))

    async def push(self, *, repo: Path, branch: str) -> None:
        await _run(repo, ["git", "push", "-u", "origin", branch])

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        await _run(repo, ["gh", "pr", "edit", "--body", body])

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # `gh pr view` with no argument already resolves from the current
        # branch, so `branch` is accepted for one Forge shape and unused here.
        # `mergeable,mergeStateStatus` ride along on the call the node already
        # makes: the check node has to know whether the PR can land, and one
        # round trip already carries it (Kraft-ejj9).
        raw = await _run(
            repo,
            [
                "gh",
                "pr",
                "view",
                "--json",
                "number,url,statusCheckRollup,mergeable,mergeStateStatus",
            ],
        )
        data = _parse_json(raw, "gh pr view")
        # Either field can carry the bad news: `mergeable` is
        # MERGEABLE/CONFLICTING/UNKNOWN, `mergeStateStatus` adds DIRTY.
        states = (str(data.get("mergeable") or ""), str(data.get("mergeStateStatus") or ""))
        mergeable = _mergeable(*states)
        detail = "/".join(s for s in states if s)
        checks = data.get("statusCheckRollup") or []
        if not checks:
            return CIStatus(
                state="pending",
                url=str(data.get("url", "")),
                jobs=("no checks yet",),
                mergeable=mergeable,
                merge_detail=detail,
            )
        jobs = tuple(f"{c.get('name')}: {c.get('conclusion') or 'PENDING'}" for c in checks)
        conclusions = [str(c.get("conclusion") or "") for c in checks]
        if any(c in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for c in conclusions):
            state: CIState = "failed"
        elif all(c in ("SUCCESS", "NEUTRAL") for c in conclusions):
            state = "success"
        else:
            state = "pending"
        return CIStatus(
            state=state,
            url=str(data.get("url", "")),
            jobs=jobs,
            mergeable=mergeable,
            merge_detail=detail,
        )

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the pull request.

        No pipeline to re-create, unlike GitLab: a workflow that cares about
        labels keys on `pull_request: types: [labeled]` and GitHub re-evaluates
        it on the edit.
        """
        if not labels:
            return
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["gh", "pr", "edit", *target, "--add-label", ",".join(labels)])

    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None:
        await _assert_pushed(repo, branch)
        target = [str(mr.number)] if mr.number > 0 else []
        await _run(repo, ["gh", "pr", "merge", *target, "--squash"])

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        raw = await _run(
            repo,
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "number,url,state",
                "-L",
                "5",
            ],
        )
        rows = _parse_json(raw, "gh pr list")
        return _pick_mr(
            [
                MRRef(
                    number=int(r["number"]),
                    url=str(r.get("url", "")),
                    state=_GH_MR_STATES.get(str(r.get("state", "")), "closed"),
                )
                for r in rows
            ]
        )


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
    validates a registry file without importing this module. Edit both together;
    that set also carries `auto`, which `backend_for` has already translated by
    the time anything calls this.
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


#: repos.yaml's `forge` (config._FORGES) -> the CLI that talks to it. Two
#: vocabularies on purpose: `forge` is a fact about the remote, the backend is
#: a fact about this machine, and a self-hosted GitLab is `gitlab` with `glab`.
_FORGE_CLI = {"gitlab": "glab", "github": "gh"}


def backend_for(backend: str, repo_forge: str | None) -> str:
    """`auto` means the forge recorded for this repo; any other name is itself.

    The registry is per install and the forge is a property of the repo, so one
    backend name in one file cannot be right for two `kraft repo connect`s. This
    is the only place that gap is closed; `resolve` stays named and never sees
    `auto`.
    """
    if backend != "auto":
        return backend
    cli = _FORGE_CLI.get(repo_forge or "")
    if cli is None:
        raise ForgeError(
            "backend: auto, but no forge is recorded for this repo — set "
            "`forge: gitlab` or `forge: github` on it in Settings → Repos "
            "(or repos.yaml), or pin a `backend:` in registry.yaml"
        )
    return cli


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
        # A branch that cannot merge is an answer, not a wait: a conflict will
        # not resolve itself in thirty minutes (Kraft-ejj9).
        if ci.state != "pending" or ci.mergeable is False:
            return ci, False
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ci, True
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * 2, cap)


async def _poll_merged(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> MRRef | None:
    """Read the merge request back until it stops being open.

    `glab mr merge --yes` exits 0 both for "merged" and for "merge when all
    merge checks pass", and the second one merges nothing (Kraft-79x3, MR !76);
    `gh pr merge` has the same shape. The exit code is not the answer — the
    merge request's own state is. Returns the last `MRRef` read, or None if the
    branch has no merge request at all any more; the caller decides from the
    state. Same backoff as `_poll_ci`, for the same reason.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cap = max(_MAX_POLL_INTERVAL, interval)
    while True:
        ref = await forge.find_mr(repo=repo, branch=branch)
        if ref is None or ref.state != "open":
            return ref
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ref
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * 2, cap)


def _describe_ci(ci: CIStatus, *, timed_out: bool, poll_timeout: float) -> tuple[str, bool]:
    """The log line(s) for one CI read, and whether it counts as a failure.

    Shared by `ci_poll` and `merge` (Kraft-266b): human_review's own commit
    re-arms the pipeline mr_checks already waited green, so `merge` has to
    poll CI again before calling `glab mr merge` rather than trust mr_checks'
    now-stale answer -- and the two nodes must describe a red, timed-out, or
    unmergeable pipeline identically rather than drift apart.
    """
    # A timeout and a red pipeline are both a failure, but a reviewer -- and
    # any fix loop built on this node (Kraft-cbr) -- has to tell "finished
    # red" from "never finished".
    head = (
        f"pipeline timed out after {poll_timeout:g}s, still pending"
        if timed_out
        else f"pipeline {ci.state}"
    )
    log = f"{head}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
    # Green *and* unmergeable is the exact shape of the bug: the pipeline
    # passes, the gate passes, and the merge node meets the conflict
    # (Kraft-ejj9). `is False` and not falsiness: None is undecided, and
    # undecided is this node's normal.
    if ci.mergeable is False:
        log += f"merge request is not mergeable: {ci.merge_detail or 'unknown'}\n"
    return log, ci.state != "success" or ci.mergeable is False


async def _run_one(
    forge: Forge,
    db,
    *,
    repo: Path,
    branch: str,
    title: str,
    work_item_id: str,
    handler: str,
    hook_point: str,
    poll_timeout: float,
    poll_interval: float,
    merge_timeout: float,
    merge_interval: float,
) -> tuple[str, str]:
    """One forge handler against one repo. Extracted from `run_task` so the
    multi-repo loop there can call it once per `work_item_repos` row; a
    single-repo item's behaviour is unchanged -- one call, same log and status
    strings as before this split.
    """
    body = mr_body(work_item_id, branch, await _commits_on(repo, branch))
    match handler:
        case "open_mr":
            # Ask first: a rejected review re-entering the chain at
            # implementation walks back through this node, and a retry of
            # an open_mr that crashed after the create lands here too. A
            # second `mr create` for one branch is an error on both CLIs
            # (Kraft-xron).
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "open":
                # The same two calls `sync_mr` makes, for the same reason:
                # the description has to describe the head a reviewer will
                # see, and the head has to be pushed.
                await forge.push(repo=repo, branch=branch)
                await forge.update_mr(repo=repo, branch=branch, body=body)
                number, url = existing.number, existing.url
                log, status = f"reusing !{number}: {url}\n", "done"
            else:
                mr = await forge.open_mr(repo=repo, branch=branch, title=title, body=body)
                number, url = mr.number, mr.url
                log, status = f"opened {url}\n", "done"
            # An event, not a work-item column (Kraft-d2sq): no migration,
            # it reaches the UI through the stream that already exists, and
            # it is timestamped, which a column is not. Both paths emit it,
            # so an item whose open_mr is re-entered after a rejected
            # review still carries a current record.
            await db.write(
                lambda c, n=number, u=url: events.append(
                    c, work_item_id, "mr_opened", {"number": n, "url": u}
                )
            )
        case "ci_poll":
            # The worker commits in the worktree and is told not to push
            # (adapters/agent.py:45), and only open_mr and sync_mr ever
            # pushed — sync_mr *after* human review. Without this, a commit
            # made after open_mr is local, so the pipeline polled below is
            # the previous head's: the same id on every retry, green or
            # red, forever (Kraft-bxj8). `git push -u` is a no-op when the
            # branch is up to date, so this costs one git call.
            await forge.push(repo=repo, branch=branch)
            # Both CLIs resolve the merge request from the checked-out
            # branch, so the number is not threaded between nodes.
            ci, timed_out = await _poll_ci(
                forge, repo=repo, branch=branch, timeout=poll_timeout, interval=poll_interval
            )
            log, failed = _describe_ci(ci, timed_out=timed_out, poll_timeout=poll_timeout)
            status = "failed" if failed else "done"
        case "sync_mr":
            # Push first: every commit after `open_mr` -- verify's fixes,
            # mr_checks' findings, the review brief -- is local only until
            # this runs, and `merge` refuses a branch ahead of its remote
            # (Kraft-nh5m).
            await forge.push(repo=repo, branch=branch)
            # The description `open_mr` wrote predates every commit verify
            # and mr_checks added, so it is rewritten from the branch head
            # before a human is asked to read it (Kraft-c09h).
            await forge.update_mr(repo=repo, branch=branch, body=body)
            log, status = "pushed and synced the merge request description\n", "done"
        case "merge":
            # "Someone merged it first" and "the merge was refused" were
            # indistinguishable while this only ever shelled out: both
            # arrived as a non-zero exit and stopped the item at the last
            # node with the work already on main (Kraft-xron). Auto-merge
            # reaching the branch first is an ordinary event, and this
            # node's stated end state is already true.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                log = f"already merged (!{existing.number}); nothing to do\n"
                status = "done"
            elif existing is not None and existing.state == "open":
                # The same push ci_poll makes, for the same reason: a
                # commit made after the last sync is local only, and
                # `_assert_pushed` inside forge.merge refuses a head origin
                # has never seen — three failed nodes, and a retry of
                # `merge` alone never re-runs mr_sync to clear it
                # (Kraft-bxj8). Below the already-merged check: a branch
                # already in main needs nothing pushed to it.
                await forge.push(repo=repo, branch=branch)
                # human_review's own commit (the review brief) lands on this
                # branch after mr_checks already waited its pipeline green,
                # which re-arms it. `glab mr merge --yes` against a pipeline
                # still running for that commit exits 0 but schedules "merge
                # when checks pass" and merges nothing (Kraft-79x3) -- so
                # mr_checks' answer is stale the moment human_review pushes,
                # and this has to ask again rather than trust it (Kraft-266b).
                ci, timed_out = await _poll_ci(
                    forge, repo=repo, branch=branch, timeout=poll_timeout, interval=poll_interval
                )
                log, failed = _describe_ci(ci, timed_out=timed_out, poll_timeout=poll_timeout)
                if failed:
                    return log, "failed"
                # A genuine refusal -- conflicts, unmet approval rules --
                # still raises inside forge.merge and still fails the node.
                await forge.merge(repo=repo, branch=branch, mr=MR(number=0, url=""))
                # And a refusal the CLI reported as success does not get
                # through either. This node's stated end state is "this
                # branch is in main", so it is read off the forge rather
                # than inferred from an exit code (Kraft-79x3).
                landed = await _poll_merged(
                    forge, repo=repo, branch=branch, timeout=merge_timeout, interval=merge_interval
                )
                state = landed.state if landed is not None else "gone"
                if state == "merged":
                    log, status = f"merged !{landed.number}\n", "done"
                elif state == "open":
                    log = (
                        f"!{landed.number} is still open {merge_timeout:g}s after the "
                        "merge command returned: nothing has landed on main. The forge "
                        "may have scheduled an auto-merge for when its checks pass — "
                        f"look at {landed.url}\n"
                    )
                    status = "failed"
                else:
                    log = f"the merge request is {state}, not merged; nothing landed\n"
                    status = "failed"
            else:
                raise ForgeError(
                    f"no open merge request for {branch!r}"
                    + (f": !{existing.number} is {existing.state}" if existing else "")
                )
        case _:
            log, status = f"unknown forge handler {handler!r}\n", "failed"
    return log, status


async def _assert_submodules_covered(repo: Path, covered: set[Path]) -> None:
    """Refuse to open the root's merge request while an initialized submodule
    holds commits no `work_item_repos` row will carry anywhere.

    The §3a scan (`builtins.scan_submodules`) runs earlier in the same node
    and is what normally covers a submodule the agent touched but nobody
    declared -- this only fires when that scan itself missed one (submodule
    init failed, `git submodule status` errored), which must stop the chain
    rather than silently drop the change, exactly as it did on work item
    9d0ab38ff3c9439b90506df0f6966660.
    """
    raw = await _run(repo, ["git", "submodule", "status"])
    for line in raw.splitlines():
        if not line or line[0] != "+":
            continue
        parts = line[1:].split()
        if len(parts) < 2:
            continue
        path = (repo / parts[1]).resolve()
        if path not in covered:
            raise ForgeError(
                f"submodule {parts[1]} has commits not covered by any declared or "
                "discovered repo — it will not reach a merge request"
            )


async def _default_branch(repo: Path) -> str:
    """origin's default branch, or `main` when the forge doesn't say."""
    try:
        raw = await _run(repo, ["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
        return raw.strip().rsplit("/", 1)[-1] or "main"
    except ForgeError:
        return "main"


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
    #: The repo entry's `forge` (`repos.yaml`), for `backend: auto`. None means
    #: nothing recorded, which `backend_for` turns into a failed node.
    repo_forge: str | None = None,
    repo: Path,
    branch: str,
    title: str,
    round: int = 0,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    #: The merge node's read-back, not the pipeline poll: separate names
    #: because they answer different questions and are tuned differently.
    #: Keyword arguments only so tests can run them at zero; no registry key.
    merge_timeout: float = MERGE_VERIFY_TIMEOUT,
    merge_interval: float = MERGE_VERIFY_INTERVAL,
) -> str:
    """One forge node -- against every repo `work_item_repos` names for this
    item, deepest submodule first, root last (design 3a), or just `repo` when
    the table has no rows for it (every single-repo item, unchanged from
    before this function went multi-repo).

    Records a session the way a builtin does rather than the way the subprocess
    adapter does: the work happens in this process, so there is no child to
    supervise and no log fd to hand over. The row still has to exist for the
    whole run, not just at the end -- `ci_poll` can sit in `_poll_ci` for up to
    `poll_timeout` (30 minutes by default), and pause/abandon/reattach all key
    off `worker_sessions`. Recording nothing until the node finished silently
    broke all three for the length of that wait (Kraft-41b, Kraft-7xt).
    """
    from kraft import builtins as _builtins
    from kraft import store

    log_path, result_path = await _builtins.start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
    )
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    multi = bool(rows)
    targets = [(r["id"], Path(r["repo_path"]), r["role"]) for r in rows] or [(None, repo, "root")]

    root_has_changes = True
    root_policy = "bump"
    if multi:
        policy_row = db.read(
            lambda c: c.execute(
                "SELECT root_merge_policy FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        root_policy = (policy_row["root_merge_policy"] if policy_row else None) or "bump"
        root_repo = next(t for _, t, role in targets if role == "root")
        root_has_changes = bool(await _commits_on(root_repo, branch))
        if root_policy == "skip" or not root_has_changes:
            # Nothing of root's own to review -- it never goes through the
            # ordinary per-repo loop below. Its pointer bump, if any (never
            # under `skip`), is pushed directly after every submodule merges
            # (below), not through a merge request -- see the plan's "Design
            # correction": at `open_mr` time no submodule has merged yet, so
            # a root MR here would review a pointer that doesn't exist.
            targets = [t for t in targets if t[2] != "root"]

    log, status = "", "done"
    try:
        # Inside the try: `backend_for` can raise, and an exception escaping
        # here would skip `finish_session` and strand the session row started
        # above (Kraft-41b, Kraft-7xt are the same wound from the other side).
        live_forge = resolve(backend_for(backend, repo_forge))
        for row_id, target_repo, role in targets:
            if handler == "open_mr" and role == "root" and multi:
                await _assert_submodules_covered(target_repo, {t for _, t, _ in targets})
            one_log, one_status = await _run_one(
                live_forge,
                db,
                repo=target_repo,
                branch=branch,
                title=title,
                work_item_id=work_item_id,
                handler=handler,
                hook_point=hook_point,
                poll_timeout=poll_timeout,
                poll_interval=poll_interval,
                merge_timeout=merge_timeout,
                merge_interval=merge_interval,
            )
            log += (f"[{target_repo.name}] " if multi else "") + one_log
            if row_id is not None:
                new_state = {
                    "open_mr": "open",
                    "merge": "merged" if one_status == "done" else "failed",
                }.get(handler)
                if new_state:
                    await db.write(
                        lambda c, i=row_id, s=new_state: store.update_repo_state(
                            c, i, merge_state=s
                        )
                    )
            if one_status != "done":
                status = one_status
                break
        if (
            handler == "merge"
            and multi
            and root_policy != "skip"
            and not root_has_changes
            and status == "done"
        ):
            # `multi` guarantees `rows` is non-empty here, so this is always
            # the root row's own path, not `repo` (the worktree root is the
            # same thing, but the row is the one source of truth).
            root_repo = Path(next(r["repo_path"] for r in rows if r["role"] == "root"))
            bumped = []
            for r in rows:
                if r["role"] != "submodule":
                    continue
                sub_path = Path(r["repo_path"])
                default = await _default_branch(sub_path)
                await _run(sub_path, ["git", "fetch", "origin", default])
                merged_sha = (
                    await _run(sub_path, ["git", "rev-parse", f"origin/{default}"])
                ).strip()
                await _run(sub_path, ["git", "checkout", merged_sha])
                rel = str(sub_path.relative_to(root_repo))
                await _run(root_repo, ["git", "add", "--", rel])
                bumped.append(rel)
            if (
                bumped
                and (await _run(root_repo, ["git", "status", "--porcelain", "--cached"])).strip()
            ):
                await _run(
                    root_repo,
                    ["git", "commit", "-m", f"chore: bump submodule pointers for {work_item_id}"],
                )
                root_default = await _default_branch(root_repo)
                await _run(root_repo, ["git", "push", "origin", f"HEAD:{root_default}"])
                log += (
                    f"bumped {', '.join(bumped)} directly on {root_default}, "
                    "no root merge request\n"
                )
    except ForgeError as exc:
        log, status = f"{hook_point} failed: {exc}\n", "failed"

    return await _builtins.finish_session(
        db, log_path, result_path, session_id=session_id, status=status, log=log
    )
