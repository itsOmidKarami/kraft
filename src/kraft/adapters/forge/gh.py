"""GitHub through `gh`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as urlquote

from kraft.adapters.forge import git
from kraft.adapters.forge import github_queries as gq
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import (
    MR,
    CIState,
    CIStatus,
    DropCause,
    FailedJob,
    ForgeError,
    HeadMoved,
    Job,
    Label,
    MergedMR,
    MRDetails,
    MRRef,
    Pipeline,
    QueueRead,
    QueueState,
    ReviewerFeedback,
    ReviewResult,
)
from kraft.adapters.forge.transport import CliTransport, Field, Transport
from kraft.automated_review import Reviewers

_GH_MR_STATES: dict[str, str] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}

#: gh's coarser conclusion vocabulary, mapped onto GitLab's `failure_reason`
#: names so `ci.is_infra_red` reads one vocabulary regardless of backend
#: (ponytail: a flat dict, not a shared enum -- add a name here if gh starts
#: distinguishing more failure shapes).
_GH_FAILURE_REASON = {
    "TIMED_OUT": "job_execution_timeout",
    "STARTUP_FAILURE": "runner_system_failure",
    "FAILURE": "script_failure",
    "ACTION_REQUIRED": "script_failure",
}
#: The conclusions that are a red verdict. Not CANCELLED: a cancelled run is
#: never a verdict (Kraft-zn8me, Kraft-7g5h4, Kraft-50bhi). GitHub cancels a
#: superseded run's jobs seconds before it registers the successor's checks, so
#: a read in that window sees only the cancelled rows. It is a wait; a run a
#: person cancelled with no successor is `ci.render_ci`'s "abandoned".
_GH_RED = frozenset(_GH_FAILURE_REASON)
_GH_SETTLED_OK = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def _bot_feedback(
    reviewer, comments: list[dict], reviews: list[dict], threads: list[dict], head: str, since
) -> ReviewerFeedback:
    """One bot's part of the pull request's comments, reviews and threads."""
    bot = reviewer.bot

    def mine(login: object) -> bool:
        return mr_ops.same_login(str(login or ""), bot)

    summaries: list[dict] = []
    seen: list[datetime | None] = []
    for c in comments:
        if mine((c.get("user") or {}).get("login")):
            at = mr_ops.forge_time(c.get("updated_at"))
            seen.append(at)
            if at is not None and at > since:
                summaries.append(
                    {
                        "kind": "comment",
                        "state": None,
                        "body": c.get("body") or "",
                        "updated_at": c["updated_at"],
                    }
                )
    for r in reviews:
        # A draft review is not submitted, and a dismissed one is a
        # maintainer's "this no longer blocks": neither is the bot speaking.
        if mine((r.get("user") or {}).get("login")) and r.get("state") not in (
            "PENDING",
            "DISMISSED",
        ):
            at = mr_ops.forge_time(r.get("submitted_at"))
            seen.append(at)
            if r.get("commit_id") == head and at is not None and at > since:
                summaries.append(
                    {
                        "kind": "review",
                        "state": r.get("state"),
                        "body": r.get("body") or "",
                        "updated_at": r["submitted_at"],
                    }
                )
    open_threads: list[dict] = []
    for t in threads:
        notes = (t.get("comments") or {}).get("nodes") or []
        seen += [
            mr_ops.forge_time(n.get("updatedAt"))
            for n in notes
            if mine((n.get("author") or {}).get("login"))
        ]
        if notes and mine((notes[0].get("author") or {}).get("login")) and not t.get("isResolved"):
            first = notes[0]
            body = "\n".join(
                [
                    first.get("body") or "",
                    *(
                        f"{(n.get('author') or {}).get('login')}: {n.get('body')}"
                        for n in notes[1:]
                    ),
                ]
            )
            open_threads.append(
                {
                    "id": t["id"],
                    "file": first.get("path"),
                    "line": first.get("line") or first.get("originalLine"),
                    "body": body,
                }
            )
    return ReviewerFeedback(reviewer, mr_ops.newest(seen), tuple(summaries), tuple(open_threads))


def _cancelled_at(rows: list[dict], *, at: str) -> str:
    """The latest cancel among `rows` when a cancel is all they wait on --
    every other row settled and none red -- else "" (Kraft-kbqmk)."""
    ends = [str(r.get("conclusion") or "").upper() for r in rows]
    if "CANCELLED" not in ends or not all(c in _GH_SETTLED_OK or c == "CANCELLED" for c in ends):
        return ""
    return max(str(r.get(at) or "") for r, c in zip(rows, ends, strict=True) if c == "CANCELLED")


_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")


def _this_repos(run: dict) -> bool:
    """A run whose head is in the repository it ran in: never a fork's."""
    head = (run.get("head_repository") or {}).get("id")
    return head is not None and head == (run.get("repository") or {}).get("id")


#: `MergeQueueEntryState` onto the forge-neutral queue states. UNMERGEABLE is
#: queued: GitHub is about to remove the entry, and the next read sees the drop.
_GH_QUEUE_STATES: dict[str, QueueState] = {
    "QUEUED": "queued",
    "AWAITING_CHECKS": "queued",
    "LOCKED": "queued",
    "UNMERGEABLE": "queued",
    "MERGEABLE": "merging",
}
_NO_REASON = "dropped from the merge queue; GitHub gave no reason"
#: A removal event this entry has, with no reason in it: GitHub says the pull
#: request left the queue but not why, which is a person's to look at.
_NO_REASON_GIVEN = "removed from the merge queue; GitHub gave no reason"
#: A removal reason that names failed or timed-out checks or CI: the code's to
#: repair even with no `merge_group` run to read (CI outside GitHub Actions).
#: Only a phrase that says the checks or CI failed or timed out: a bare "ci",
#: "check" or "fail" is in "Removed by ci-bot" or "to check something" too, and
#: reading those as a red build would re-queue what a person took out.
#: Words part on a space, `_` or `-`, and a word ends at anything but a letter
#: or digit, so an enum-style token (`FAILED_CHECKS`) reads as the phrase does.
_CHECKS_FAILED = re.compile(
    (
        r"(?<![a-z0-9])(?:"
        r"(?:status_)?checks?_(?:(?:have|has)_)?(?:failed|timed_out)"
        r"|(?:failed|failing)_(?:(?:required_)?status_)?checks?"
        # `\x5f` is `_` itself: "timeout", "time-out" and "time_out" alike.
        r"|checks?_time[\s\x5f-]?outs?"
        r"|ci_(?:(?:has|have)_)?(?:failed|failure|timed_out)"
        r"|failed_ci|ci_failures?"
        r"|timed_out_waiting_for_(?:(?:required_)?status_)?checks?"
        r")(?![a-z0-9])"
    ).replace("_", r"[\s_-]+"),
    re.IGNORECASE,
)
#: A removal reason that names a person, or a removal by hand: theirs to
#: decide, whatever the merge group's runs say. Words part as above.
_REMOVED_BY_HAND = re.compile(
    r"(?<![a-z0-9])(?:removed_by|dequeued_by|manual(?:ly)?|by_hand)(?![a-z0-9])".replace(
        "_", r"[\s_-]+"
    ),
    re.IGNORECASE,
)

#: What `cancel_merge` reads: the pull request's id, its auto-merge request and
#: its merge queue entry.
_CANCEL_QUERY = """
query($owner: String!, $name: String!, $n: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $n) {
      id
      autoMergeRequest { enabledAt }
      mergeQueueEntry { id }
    }
  }
}
"""
#: A pull request's review threads, with each one's comments: what
#: `reviewer_feedback` reads, since REST review comments carry no resolved flag.
_THREADS_QUERY = """
query($owner: String!, $name: String!, $n: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $n) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 100) {
            nodes { author { login } body path line originalLine updatedAt }
          }
        }
      }
    }
  }
}
"""
_READY_MUTATION = """
mutation($id: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { id } }
}
"""
_DISABLE_AUTO_MUTATION = """
mutation($id: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $id}) { pullRequest { id } }
}
"""
#: What `merge` sends, as gh does for `pr merge --squash --match-head-commit`
#: (http.go:47-103): the same input to either mutation, and nothing more.
_MERGE_INPUT = "input: {pullRequestId: $id, mergeMethod: SQUASH, expectedHeadOid: $head}"
_MERGE_MUTATION = f"""
mutation($id: ID!, $head: GitObjectID!) {{
  mergePullRequest({_MERGE_INPUT}) {{ pullRequest {{ id }} }}
}}
"""
_AUTO_MERGE_MUTATION = f"""
mutation($id: ID!, $head: GitObjectID!) {{
  enablePullRequestAutoMerge({_MERGE_INPUT}) {{ pullRequest {{ id }} }}
}}
"""
#: merge.go:379-397: the states gh refuses to merge without `--auto`/`--admin`.
_BLOCKED = {
    "BLOCKED": "the base branch policy prohibits the merge",
    "BEHIND": "the head branch is not up to date with the base branch",
    "DIRTY": "the merge commit cannot be cleanly created",
}
_DEQUEUE_MUTATION = """
mutation($id: ID!) {
  dequeuePullRequest(input: {id: $id}) { mergeQueueEntry { id } }
}
"""

#: The branch GitHub builds a queued pull request's merge group on.
_QUEUE_BRANCH = "gh-readonly-queue/{base}/pr-{number}-"
_QUEUE_QUERY = """
query($owner: String!, $name: String!, $base: String!, $n: Int!) {
  repository(owner: $owner, name: $name) {
    mergeQueue(branch: $base) { id }
    pullRequest(number: $n) {
      state
      mergeQueueEntry { state position }
      removals: timelineItems(last: 1, itemTypes: [REMOVED_FROM_MERGE_QUEUE_EVENT]) {
        nodes { ... on RemovedFromMergeQueueEvent { reason createdAt enqueuer { login } } }
      }
      enqueues: timelineItems(last: 1, itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT]) {
        nodes { ... on AddedToMergeQueueEvent { createdAt } }
      }
    }
  }
}
"""
#: What a run's job list stands in for when it could not be read: a failure
#: that is code-red until proven otherwise, never `ci.is_infra_red`'s empty
#: list (the forge confirmed no job ran).
_UNREADABLE_JOBS = (FailedJob("(unreadable)", "failed", None),)


def _newest_node(items: object) -> dict:
    """The last node of a `timelineItems` alias, else an empty one."""
    nodes = items.get("nodes") if isinstance(items, dict) else None
    return next((n for n in reversed(nodes or []) if isinstance(n, dict)), {})


def _newest_at(items: object) -> str:
    return str(_newest_node(items).get("createdAt") or "")


def _red_jobs(rows: list[dict], *, url: str) -> tuple[FailedJob, ...]:
    """A `FailedJob` for every red row of `rows` (check or workflow-job rows
    alike): its `conclusion` coarsened by `_GH_FAILURE_REASON`, and the link
    `retry_jobs` reads the run id from, which row field `url` names."""
    return tuple(
        FailedJob(
            str(r.get("name", "")),
            "failed",
            _GH_FAILURE_REASON.get(str(r.get("conclusion") or "").upper()),
            str(r.get(url) or "") or None,
        )
        for r in rows
        if str(r.get("conclusion") or "").upper() in _GH_RED
    )


def _latest_by_name(rows: list[dict], *, name: str, started: str) -> list[dict]:
    """Collapse to one row per check name, keeping the one with the latest
    `started` timestamp.

    `test.yml` deliberately re-triggers on `labeled`/`unlabeled` (release-impact
    needs to see label changes), so a label Kraft sets mid-check starts a
    second workflow run on the same PR. `statusCheckRollup` and the workflow-run list
    both then carry entries from *both* runs -- the superseded run's jobs
    (often `CANCELLED` by the `cancel-in-progress` concurrency group) alongside
    the new run's real result, same name, two conclusions. Without this, a
    stale `CANCELLED`/`FAILURE` row outlives its own run and pins `ci_status`
    red forever, however green the latest run finishes.
    """
    latest: dict[str, dict] = {}
    for row in rows:
        key = str(row.get(name) or "")
        prev = latest.get(key)
        if prev is None or str(row.get(started) or "") >= str(prev.get(started) or ""):
            latest[key] = row
    return list(latest.values())


class GhCli(mr_ops.CliWaits):
    """GitHub through `gh`. For the public repo after the v0.1.0 split."""

    def __init__(self, transport: Transport | None = None):
        self._injected = transport

    def _transport(self, repo: Path) -> Transport:
        return self._injected or CliTransport("gh", repo)

    async def _api(
        self,
        repo: Path,
        path: str,
        *,
        method: str = "GET",
        fields: Mapping[str, Field] | None = None,
    ):
        """The parsed JSON of one REST call."""
        return (await self._transport(repo).rest(method, path, fields=fields)).json()

    async def _send(
        self, repo: Path, path: str, *, method: str, fields: Mapping[str, Field] | None = None
    ) -> None:
        """One REST write whose answer is not read (some have an empty body)."""
        await self._transport(repo).rest(method, path, fields=fields)

    async def _require_labels(self, repo: Path, labels: tuple[str, ...]) -> None:
        """Refuse a label the repository does not have, as gh does before it
        writes anything; GitHub would not create it."""
        for name in labels:
            try:
                await self._api(repo, f"repos/{{owner}}/{{repo}}/labels/{urlquote(name, safe='')}")
            except ForgeError as exc:
                if exc.status == 404:
                    raise ForgeError(f"could not add label: '{name}' not found") from exc
                raise

    async def _gql(self, repo: Path, query: str, variables: Mapping[str, str | int | bool]) -> dict:
        """The `data` of one GraphQL call."""
        return await self._transport(repo).graphql(query, variables)

    async def open_mr(
        self,
        *,
        repo: Path,
        branch: str,
        base: str,
        title: str,
        body: str,
        meta: mr_ops.MRMeta | None = None,
        check_clean: bool = True,
    ) -> MR:
        if check_clean:
            await git.assert_clean(repo, base)
        await self.push(repo=repo, branch=branch)
        meta = meta or mr_ops.MRMeta()
        await self._require_labels(repo, meta.labels)
        made = await self._api(
            repo,
            "repos/{owner}/{repo}/pulls",
            method="POST",
            # Draft: see GlabCli.open_mr.
            fields={
                "head": branch,
                "base": base,
                "title": mr_ops.mr_title(title),
                "body": body,
                "draft": True,
            },
        )
        try:
            number, url = int(made["number"]), str(made["html_url"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ForgeError(f"gh api answered without the pull request: {made!r:.200}") from exc
        # Create metadata is follow-up calls in gh too.
        if meta.labels:
            await self._send(
                repo,
                f"repos/{{owner}}/{{repo}}/issues/{number}/labels",
                method="POST",
                fields={"labels": list(meta.labels)},
            )
        if meta.assignees:
            await self._send(
                repo,
                f"repos/{{owner}}/{{repo}}/issues/{number}/assignees",
                method="POST",
                fields={"assignees": list(meta.assignees)},
            )
        if meta.reviewers:
            people = [r for r in meta.reviewers if "/" not in r]
            teams = [r.split("/", 1)[1] for r in meta.reviewers if "/" in r]
            await self._send(
                repo,
                f"repos/{{owner}}/{{repo}}/pulls/{number}/requested_reviewers",
                method="POST",
                fields={
                    **({"reviewers": people} if people else {}),
                    **({"team_reviewers": teams} if teams else {}),
                },
            )
        return MR(number=number, url=url)

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        """Nothing to do for a pull request that is not a draft, as `gh pr
        ready` (same reasoning as `GlabCli.mark_ready`)."""
        pr = await self._pr(repo, mr_ops.target(mr.number, branch), checks=False)
        if pr.get("isDraft"):
            await self._gql(repo, _READY_MUTATION, {"id": pr["id"]})

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        pr = await self._pr(repo, mr_ops.target(0, branch), checks=False)
        await self._send(
            repo,
            f"repos/{{owner}}/{{repo}}/pulls/{pr['number']}",
            method="PATCH",
            fields={"body": body},
        )

    async def mr_body(self, *, repo: Path, branch: str) -> str:
        data = await self._pr(repo, mr_ops.target(0, branch), checks=False)
        return str(data.get("body") or "")

    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        # `pipeline_id` is a GitLab-only concept (Kraft-ivh1): GitHub's checks
        # are per-PR-head, not per-pipeline, so there is nothing to pin to and
        # this is accepted only for signature symmetry.
        # `mergeable,mergeStateStatus` ride along on the call the node already
        # makes: the check node has to know whether the PR can land, and one
        # round trip already carries it (Kraft-ejj9).
        data = await self._pr(repo, mr_ops.target(mr.number, branch))
        # Either field can carry the bad news: `mergeable` is
        # MERGEABLE/CONFLICTING/UNKNOWN, `mergeStateStatus` adds DIRTY.
        # `reviewDecision` rides along for the same reason `mergeable`/
        # `mergeStateStatus` do: `mergeStateStatus: BLOCKED` alone doesn't
        # say whether that's a missing approval or something else, and
        # `merge` has to tell those apart (draft-MR workflow spec).
        states = (
            str(data.get("mergeable") or ""),
            str(data.get("mergeStateStatus") or ""),
            str(data.get("reviewDecision") or ""),
        )
        mergeable = mr_ops.mergeable(*states)
        block_reason = mr_ops.classify_block_reason(*states)
        detail = "/".join(s for s in states if s)
        # Not `headRefOid`: that is the PR's own head, the very value render_ci's
        # freshness guard compares against, so it could never disagree. The sha
        # is the head commit the rollup was read from, once an Actions run is in
        # it, else "" (a rollup of commit statuses alone names no run).
        rollup = data.get("statusCheckRollup") or []
        sha = (
            str(data.get("headCommitOid") or "")
            if any(c.get("workflowRunId") for c in rollup)
            else ""
        )
        # gh's "no checks yet" (empty statusCheckRollup) stays "pending",
        # unchanged -- that is a precursor state (checks not yet registered),
        # not GitLab's *settled*-red-with-zero-jobs case Kraft-ddxn is about,
        # so it is not treated as infra here.
        checks = _latest_by_name(rollup, name="name", started="startedAt")
        if not checks:
            return CIStatus(
                state="pending",
                url=str(data.get("url", "")),
                jobs=("no checks yet",),
                no_checks=True,
                mergeable=mergeable,
                merge_detail=detail,
                block_reason=block_reason,
                sha=sha,
            )
        jobs = tuple(f"{c.get('name')}: {c.get('conclusion') or 'PENDING'}" for c in checks)
        conclusions = [str(c.get("conclusion") or "") for c in checks]
        if any(c in _GH_RED for c in conclusions):
            state: CIState = "failed"
        elif all(c in _GH_SETTLED_OK for c in conclusions):
            state = "success"
        else:
            state = "pending"
        failed_jobs = _red_jobs(checks, url="detailsUrl")
        return CIStatus(
            state=state,
            url=str(data.get("url", "")),
            jobs=jobs,
            mergeable=mergeable,
            merge_detail=detail,
            block_reason=block_reason,
            sha=sha,
            failed_jobs=failed_jobs,
            cancelled_at=_cancelled_at(checks, at="completedAt"),
        )

    async def _pr(
        self, repo: Path, target: list[str], *, checks: bool = True, fields: str = ""
    ) -> dict:
        """The pull request `target` names (`mr_ops.target`), read through the
        transport; `checks=False` leaves the check rollup unread, and `fields`
        selects those instead (no rollup)."""
        return await gq.read_pr(self._transport(repo), repo, target, checks=checks, fields=fields)

    async def _bot_review(self, repo: Path, branch: str, bot: str) -> ReviewResult:
        """`bot`'s latest review of the PR's current head, dismissed ones
        skipped: pending until there is one. Changes requested, or any inline comment, is actionable
        -- one finding per comment; anything else is clean."""
        pr = await self._pr(repo, mr_ops.target(0, branch), checks=False)
        number, head = pr["number"], pr["headRefOid"]
        # ponytail: one page of 100 reviews -- paginate if a PR ever has more.
        reviews = await self._api(
            repo, f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews", fields={"per_page": 100}
        )
        mine = [
            r
            for r in reviews
            if mr_ops.same_login(str((r.get("user") or {}).get("login", "")), bot)
            and r.get("commit_id") == head
            # A draft review is not submitted, and a dismissed one is a
            # maintainer's "this no longer blocks" (Kraft-mlicj): neither
            # counts, so the bot's latest other review of this head stands.
            and r.get("state") not in ("PENDING", "DISMISSED")
        ]
        if not mine:
            return ReviewResult("pending", detail=f"waiting for {bot} to review {head[:7]}")
        last = mine[-1]
        comments = await self._api(
            repo,
            f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews/{last['id']}/comments",
            fields={"per_page": 100},
        )
        findings = tuple(
            f"{c.get('path')}:{c.get('line') or c.get('original_line')}: {c.get('body', '')}"
            for c in comments
        )
        if last.get("state") == "CHANGES_REQUESTED" and not findings:
            findings = (str(last.get("body") or f"{bot} requested changes"),)
        if findings:
            return ReviewResult("actionable", findings=findings, detail=f"{bot}: {last['state']}")
        return ReviewResult("clean", detail=f"{bot}: {last.get('state')}")

    async def reviewer_feedback(
        self, *, repo: Path, branch: str, reviewers: Reviewers, since: datetime
    ) -> list[ReviewerFeedback]:
        """What each of `reviewers` has said on the pull request, in order. The
        pull request, and for the bots its comments, reviews and threads, are
        read once however many reviewers there are."""
        pr = await self._pr(repo, mr_ops.target(0, branch), checks=False)
        number, head = pr["number"], pr["headRefOid"]
        comments = reviews = threads = []
        if any(r.bot is not None for r in reviewers):
            # ponytail: one page of 100 of each -- paginate if a PR ever has more.
            comments = await self._api(
                repo, f"repos/{{owner}}/{{repo}}/issues/{number}/comments", fields={"per_page": 100}
            )
            reviews = await self._api(
                repo, f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews", fields={"per_page": 100}
            )
            data = await self._gql(
                repo, _THREADS_QUERY, {"owner": "{owner}", "name": "{repo}", "n": number}
            )
            try:
                threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            except (KeyError, TypeError) as exc:
                raise ForgeError(
                    f"gh api graphql answered without the review threads: {data!r:.200}"
                ) from exc
        return [
            _bot_feedback(r, comments, reviews, threads, head, since)
            if r.bot is not None
            else ReviewerFeedback(r, None, check=await self._check_feedback(repo, head, r.check))
            for r in reviewers
        ]

    async def _check_feedback(self, repo: Path, head: str, check: str) -> dict:
        """The check run `check` on `head`, or else the commit status of that
        name, as `{"name", "finished", "conclusion", "output"}`. A cancelled run
        is never finished: GitHub starts its successor a moment later."""
        runs = (
            await self._api(
                repo,
                f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs",
                fields={"check_name": check},
            )
        ).get("check_runs") or []
        if runs:
            run = runs[0]
            conclusion = str(run.get("conclusion") or "")
            output = run.get("output") or {}
            return {
                "name": check,
                "finished": run.get("status") == "completed" and conclusion != "cancelled",
                "conclusion": conclusion or None,
                "output": "\n".join(
                    str(output[k]) for k in ("title", "summary", "text") if output.get(k)
                ),
            }
        statuses = (await self._api(repo, f"repos/{{owner}}/{{repo}}/commits/{head}/status")).get(
            "statuses"
        ) or []
        status = next((s for s in statuses if s.get("context") == check), None)
        state = None if status is None else status.get("state")
        return {
            "name": check,
            "finished": state is not None and state != "pending",
            "conclusion": state,
            "output": str((status or {}).get("description") or ""),
        }

    async def _check_review(self, repo: Path, branch: str, check: str) -> ReviewResult:
        """The check run `check` on the PR's head, or else the commit status
        of that name: pending until it completes, clean on success, and
        actionable on anything else, its output the finding."""
        pr = await self._pr(repo, mr_ops.target(0, branch), checks=False)
        head = pr["headRefOid"]
        runs = (
            await self._api(
                repo,
                f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs",
                fields={"check_name": check},
            )
        ).get("check_runs") or []
        if runs:
            run = runs[0]
            if run.get("status") != "completed":
                return ReviewResult("pending", detail=f"{check}: {run.get('status')}")
            conclusion = str(run.get("conclusion") or "")
            if conclusion in ("success", "neutral", "skipped"):
                return ReviewResult("clean", detail=f"{check}: {conclusion}")
            if conclusion == "cancelled":  # never a verdict, as in `ci_status`
                return ReviewResult("pending", detail=f"{check}: cancelled")
            output = run.get("output") or {}
            text = "\n".join(str(output[k]) for k in ("title", "summary", "text") if output.get(k))
            return ReviewResult("actionable", findings=(f"{check} {conclusion}: {text}",))
        statuses = (await self._api(repo, f"repos/{{owner}}/{{repo}}/commits/{head}/status")).get(
            "statuses"
        ) or []
        status = next((s for s in statuses if s.get("context") == check), None)
        if status is None or status.get("state") == "pending":
            return ReviewResult("pending", detail=f"waiting for {check} on {head[:7]}")
        if status.get("state") == "success":
            return ReviewResult("clean", detail=f"{check}: success")
        return ReviewResult(
            "actionable",
            findings=(f"{check} {status.get('state')}: {status.get('description') or ''}",),
        )

    async def branch_ci_status(
        self, *, repo: Path, branch: str, head_sha: str, pipeline_id: str = ""
    ) -> CIStatus:
        """The target branch's own CI, not a pull request's.

        `merge_watch` calls this once the merge has landed: the checkout's
        current branch normally has no open PR left for `ci_status`'s pull-request
        read to resolve, and it would raise instead of reading the branch's
        runs at all. The branch run list reads the branch's own workflow
        runs directly, no PR involved, those of `head_sha` alone. `pipeline_id`
        is unused, same reason as in `ci_status`: gh's checks are per-commit,
        not per-pipeline.
        `head_sha` is the caller's own freshly-fetched upstream head, not this
        checkout's possibly-stale local HEAD, so it is what runs are matched
        against here.
        """
        # That commit's own runs, not the branch's newest 20: on a busy
        # branch the commit a caller keeps watching falls out of that
        # window and would read as never having run (Kraft-h6wi2).
        runs = gq.run_rows(
            await self._api(
                repo,
                "repos/{owner}/{repo}/actions/runs",
                fields={"branch": branch, "head_sha": head_sha, "per_page": 20},
            )
        )
        # The latest run of each workflow for this head, so a cancelled
        # superseded run is outvoted by its successor, as in `ci_status`.
        current = _latest_by_name(
            [r for r in runs if str(r.get("headSha") or "") == head_sha],
            name="name",
            started="createdAt",
        )
        if not current:
            return CIStatus(
                state="pending",
                url="",
                jobs=(f"no run for {head_sha[:7]} yet",),
                no_checks=not await self._reports_elsewhere(repo, head_sha),
            )
        jobs = tuple(
            f"{r.get('name')}: {r.get('conclusion') or r.get('status') or 'PENDING'}"
            for r in current
        )
        if any(str(r.get("status") or "") != "completed" for r in current):
            state: CIState = "pending"
        else:
            conclusions = [str(r.get("conclusion") or "").upper() for r in current]
            if any(c in _GH_RED for c in conclusions):
                state = "failed"
            elif all(c in _GH_SETTLED_OK for c in conclusions):
                state = "success"
            else:
                state = "pending"
        failed_jobs = _red_jobs(current, url="url")
        return CIStatus(
            state=state,
            url=str(current[0].get("url", "")),
            jobs=jobs,
            sha=head_sha,
            failed_jobs=failed_jobs,
            cancelled_at=_cancelled_at(current, at="updatedAt"),
        )

    async def _reports_elsewhere(self, repo: Path, sha: str) -> bool:
        """Whether `sha` has a check run or commit status from anything, so
        CI that is not Actions (Buildkite, CircleCI, the status API) keeps the
        wait pending instead of passing as "nothing checked". True when either
        read fails: an unreadable answer is a reason to wait, never to pass."""
        try:
            runs = await self._api(
                repo, f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs", fields={"per_page": 1}
            )
            if runs.get("check_runs"):
                return True
            status = await self._api(repo, f"repos/{{owner}}/{{repo}}/commits/{sha}/status")
            return bool(status.get("statuses"))
        except (ForgeError, AttributeError):
            return True

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        """Rerun the failed jobs of every distinct Actions run behind
        `ci`'s failed checks. Best-effort: a check with no `/actions/runs/<id>/`
        in its `detailsUrl` (a non-Actions status check) is skipped."""
        run_ids = {
            m.group(1)
            for job in ci.failed_jobs
            if job.detail_url and (m := _RUN_ID_RE.search(job.detail_url))
        }
        for run_id in run_ids:
            await self._send(
                repo,
                f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/rerun-failed-jobs",
                method="POST",
            )

    async def rerun_after_description_change(
        self, *, repo: Path, branch: str, ci: CIStatus
    ) -> bool:
        """A job that reads the description through the API sees the edit on
        a re-run, and a workflow on `pull_request: edited` re-runs by itself."""
        await self.retry_jobs(repo=repo, ci=ci)
        return False

    async def set_labels(self, *, repo: Path, branch: str, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the pull request.

        No pipeline to re-create, unlike GitLab: a workflow that cares about
        labels keys on `pull_request: types: [labeled]` and GitHub re-evaluates
        it on the edit.

        A label that shares a new label's `scope::` prefix is removed in the
        same edit, as `GlabCli.set_labels` does (Kraft-o9xh1).
        """
        if not labels:
            return
        target = mr_ops.target(mr.number, branch)
        data = await self._pr(repo, target, checks=False)
        current = [
            str(label.get("name", "")) for label in (data.get("labels") or {}).get("nodes") or []
        ]
        await self._require_labels(repo, labels)
        number = data["number"]
        await self._send(
            repo,
            f"repos/{{owner}}/{{repo}}/issues/{number}/labels",
            method="POST",
            fields={"labels": list(labels)},
        )
        for label in mr_ops.same_scope_labels(current, labels):
            try:
                await self._send(
                    repo,
                    f"repos/{{owner}}/{{repo}}/issues/{number}/labels/{urlquote(label, safe='')}",
                    method="DELETE",
                )
            except ForgeError as exc:
                if exc.status != 404:  # taken off the pull request since it was read
                    raise

    async def merge(self, *, repo: Path, branch: str, mr: MR, head: str) -> None:
        """Squash-merge at `head` only, as gh decides (merge.go:151-304): an
        already queued pull request is left alone, one whose base has a merge
        queue is queued through auto-merge, a blocked, behind or conflicting one
        is refused with nothing sent, and any other is merged. GitHub refuses a
        pull request whose head is not `head`, however long after this call it
        would have merged: that is `HeadMoved`, with the head GitHub holds."""
        if not head:
            raise ForgeError("cannot merge without the head to merge")
        await git._assert_pushed(repo, branch)
        target = mr_ops.target(mr.number, branch)
        try:
            pr = await self._pr(repo, target, fields=gq.MERGE_FIELDS)
        except ForgeError as exc:
            # gh feature-detects the queue fields (finder.go:212-226); an older
            # GitHub Enterprise Server has none, and so no queue to enter.
            if not any(f in str(exc) for f in ("isMergeQueueEnabled", "isInMergeQueue")):
                raise
            pr = await self._pr(repo, target, fields=gq.PR_CORE_FIELDS)
        # merge.go:590,257,282-284: a pull request that landed since it was read is done.
        if pr.get("isInMergeQueue") or pr.get("state") == "MERGED":
            return
        if pr.get("isMergeQueueEnabled"):
            mutation = _AUTO_MERGE_MUTATION
        elif refused := _BLOCKED.get(str(pr.get("mergeStateStatus"))):
            raise ForgeError(f"pull request #{pr.get('number')} is not mergeable: {refused}")
        else:
            mutation = _MERGE_MUTATION
        try:
            await self._gql(repo, mutation, {"id": pr["id"], "head": head})
        except ForgeError as exc:
            if exc.reason != "head_moved":
                raise
            # GitHub's wording is matched, not known: an error that names the head
            # but leaves the pull request at `head` is not a head that moved.
            current = await self._forge_head(repo, branch)
            if current == head:
                raise
            raise HeadMoved(str(exc), current) from exc

    async def _forge_head(self, repo: Path, branch: str) -> str:
        """The pull request's head as GitHub holds it, "" when it cannot be read."""
        try:
            found = await self.find_mr(repo=repo, branch=branch)
        except ForgeError:
            return ""
        return found.head_sha if found else ""

    async def cancel_merge(self, *, repo: Path, branch: str, mr: MRRef) -> None:
        """Take the pull request out of whatever would merge it: auto-merge is
        disabled, and a merge queue entry dequeued. Each is read first, so a
        request that is neither is left alone, and a call that fails raises."""
        data = await self._gql(
            repo, _CANCEL_QUERY, {"owner": "{owner}", "name": "{repo}", "n": mr.number}
        )
        try:
            pull = data["repository"]["pullRequest"]
            auto, queued, node = pull["autoMergeRequest"], pull["mergeQueueEntry"], pull["id"]
        except (KeyError, TypeError) as exc:
            raise ForgeError(
                f"gh api graphql answered without the pull request: {data!r:.200}"
            ) from exc
        if auto:
            await self._gql(repo, _DISABLE_AUTO_MUTATION, {"id": node})
        if queued:
            await self._gql(repo, _DEQUEUE_MUTATION, {"id": node})

    async def close_mr(self, *, repo: Path, mr: MRRef) -> None:
        await self._send(
            repo,
            f"repos/{{owner}}/{{repo}}/pulls/{mr.number}",
            method="PATCH",
            fields={"state": "closed"},
        )

    async def reopen_mr(self, *, repo: Path, mr: MRRef) -> None:
        await self._send(
            repo,
            f"repos/{{owner}}/{{repo}}/pulls/{mr.number}",
            method="PATCH",
            fields={"state": "open"},
        )

    async def merge_queue(self, *, repo: Path, branch: str, base: str) -> QueueRead:
        """Where this branch's pull request stands in `base`'s merge queue.

        One GraphQL read: whether `base` has a queue at all, the pull request's
        state and queue entry, and the last time GitHub removed it. A call that
        fails raises: only a `null` queue is "no queue", and only a missing entry
        on an open pull request is a drop."""
        mr = await self.find_mr(repo=repo, branch=branch)
        if mr is None:
            return QueueRead()
        data = await self._gql(
            repo,
            _QUEUE_QUERY,
            # A `str` goes as `-f`, so a base named `123` or `null` stays a string.
            {"owner": "{owner}", "name": "{repo}", "base": base, "n": mr.number},
        )
        try:
            repository = data["repository"]
            queued = repository["mergeQueue"]
            pull = repository["pullRequest"]
        except (KeyError, TypeError) as exc:
            raise ForgeError(
                f"gh api graphql answered without the merge queue: {data!r:.200}"
            ) from exc
        if not queued:
            return QueueRead()
        if not isinstance(pull, dict):
            return QueueRead(kind="queue", url=mr.url)
        if pull.get("state") == "MERGED":
            return QueueRead(kind="queue", state="merged", url=mr.url)
        entry = pull.get("mergeQueueEntry")
        if isinstance(entry, dict):
            state = _GH_QUEUE_STATES.get(str(entry.get("state")), "queued")
            return QueueRead(kind="queue", state=state, url=mr.url)
        if pull.get("state") != "OPEN":
            return QueueRead(kind="queue", url=mr.url)
        return await self._dropped(repo, pull, mr, base)

    async def _dropped(self, repo: Path, pull: dict, mr: MRRef, base: str) -> QueueRead:
        """A pull request GitHub took out of the queue: why, in GitHub's words,
        and the merge group's failed run when there is one."""
        enqueued_at = _newest_at(pull.get("enqueues"))
        removal = _newest_node(pull.get("removals"))
        # A removal older than the latest enqueue belongs to an earlier entry:
        # it says nothing about why this one is gone (ISO times sort as text).
        # No removal event at all for this entry yet -- or a pull request
        # about to show merged -- is no reason, which `run._merge_dropped`
        # reads again next poll rather than as a person's removal.
        removed = bool(removal) and str(removal.get("createdAt") or "") >= enqueued_at
        reason = str(removal.get("reason") or "").strip() if removed else ""
        failure = await self._merge_group_failure(repo, base, mr.number, since=enqueued_at)
        # GitHub's own words first: a person's removal is theirs whatever the
        # runs say (a run that failed and was rebuilt green is no verdict on a
        # removal by hand). The merge group's run is evidence only when the
        # reason names failed checks or says nothing.
        cause: DropCause = "other"
        if _REMOVED_BY_HAND.search(reason):
            cause = "other"
        elif "conflict" in reason.lower():
            cause = "conflict"
        elif _CHECKS_FAILED.search(reason) or (not reason and failure is not None):
            cause = "code"
        if cause == "code":
            reason = reason or _NO_REASON
        elif removed and not reason:
            reason = _NO_REASON_GIVEN
        return QueueRead(
            kind="queue",
            state="dropped",
            cause=cause,
            reason=reason,
            failure=failure,
            url=(failure.url if failure is not None else "") or mr.url,
        )

    async def _merge_group_failure(
        self, repo: Path, base: str, number: int, *, since: str
    ) -> CIStatus | None:
        """The newest `merge_group` run for pull request `number` made at or
        after `since` (the latest enqueue; "" when unknown), read as a red
        `CIStatus` when it failed, else None. An earlier entry's failed run is
        not this entry's: blaming it would send a repair at code already
        fixed. Nor is a failed run GitHub rebuilt since -- the group failed
        for a pull request ahead, and the rebuild went green, is still
        running, or was cancelled when a person took the pull request out --
        so only the newest run is a verdict, whatever its status: a running
        or cancelled one is none (`_GH_RED`), never a reason to look past it.

        The runs list is repo-wide, so it reads a hundred runs, not a few.

        Best effort: a drop was already established, and a run that cannot be
        listed leaves GitHub's own removal reason as the only finding."""
        try:
            rows = gq.run_rows(
                await self._api(
                    repo,
                    "repos/{owner}/{repo}/actions/runs",
                    fields={"event": "merge_group", "per_page": 100},
                )
            )
        except ForgeError:
            return None
        prefix = _QUEUE_BRANCH.format(base=base, number=number)
        entry_runs = [
            r
            for r in rows
            if isinstance(r, dict)
            and str(r.get("headBranch") or "").startswith(prefix)
            and str(r.get("createdAt") or "") >= since
        ]
        if not entry_runs:
            return None
        # Newest first as `gh` lists them; `max` keeps the first of a tie.
        run = max(entry_runs, key=lambda r: str(r.get("createdAt") or ""))
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion.upper() not in _GH_RED:
            return None
        run_id = str(run.get("databaseId") or "")
        url = str(run.get("url") or "")
        jobs, failed_jobs = await self._run_failure(repo, run_id)
        return CIStatus(
            state="failed",
            url=url,
            jobs=(f"run {run_id}: {conclusion}", *jobs),
            failed_jobs=failed_jobs,
        )

    async def _run_failure(
        self, repo: Path, run_id: str
    ) -> tuple[tuple[str, ...], tuple[FailedJob, ...]]:
        """A failed run's red jobs, and the tail of the first two jobs' failed
        steps' logs. A job list that cannot be read is `_UNREADABLE_JOBS`, not
        an empty one, which `ci.is_infra_red` would read as a runner problem."""
        if not run_id:
            return (), _UNREADABLE_JOBS
        try:
            # strict: a capped list is not the whole run, and a missing "jobs" raises
            data = await self._paged(
                repo,
                f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs",
                key="jobs",
                strict=True,
            )
        except ForgeError:
            return (), _UNREADABLE_JOBS
        rows = [j for j in data if isinstance(j, dict)]
        failed_jobs = _red_jobs(rows, url="html_url")
        red = [j for j in rows if str(j.get("conclusion") or "").upper() in _GH_RED]
        detail: tuple[str, ...] = ()
        # ponytail: first two failed jobs, last 15 log lines each, as `glab`.
        for job, row in zip(failed_jobs[:2], red[:2], strict=True):
            reason = job.failure_reason
            detail += (f"job {job.name}: failed" + (f" ({reason})" if reason else ""),)
            try:
                whole = await self._transport(repo).rest(
                    "GET",
                    f"repos/{{owner}}/{{repo}}/actions/jobs/{row.get('id')}/logs",
                    tail=True,
                    download=True,
                )
            except ForgeError:
                continue
            log = gq.failed_step_log(row, whole.text)
            detail += tuple(f"  {line}" for line in log.strip().splitlines()[-15:])
        return detail, failed_jobs

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        data = await self._gql(repo, gq.FIND_QUERY, gq.owner_vars(b=branch))
        try:
            rows = data["repository"]["pullRequests"]["nodes"]
        except (KeyError, TypeError) as exc:
            raise ForgeError(
                f"gh api graphql answered without the pull requests: {data!r:.200}"
            ) from exc
        return mr_ops.pick_mr(
            [
                MRRef(
                    number=int(r["number"]),
                    url=str(r.get("url", "")),
                    state=_GH_MR_STATES.get(str(r.get("state", "")), "closed"),
                    merge_queued=bool(r.get("autoMergeRequest")),
                    merged_sha=str((r.get("mergeCommit") or {}).get("oid") or ""),
                    head_sha=str(r.get("headRefOid") or ""),
                )
                for r in rows
                if isinstance(r, dict)
            ]
        )

    async def mr_details(self, *, repo: Path, branch: str) -> MRDetails | None:
        ref = await self.find_mr(repo=repo, branch=branch)
        if ref is None:
            return None
        pull = await self._api(repo, f"repos/{{owner}}/{{repo}}/pulls/{ref.number}")
        return MRDetails(
            number=ref.number,
            url=ref.url,
            state="merged" if pull.get("merged_at") else ref.state,
            title=str(pull.get("title") or ""),
            body=str(pull.get("body") or ""),
            draft=bool(pull.get("draft")),
            labels=tuple(str(x.get("name")) for x in pull.get("labels") or []),
            source_branch=str((pull.get("head") or {}).get("ref") or ""),
            target_branch=str((pull.get("base") or {}).get("ref") or ""),
            head_sha=str((pull.get("head") or {}).get("sha") or ""),
        )

    async def pipelines(self, *, repo: Path, branch: str, base: str) -> tuple[Pipeline, ...]:
        runs = "repos/{owner}/{repo}/actions/runs"
        # `branch=` matches a fork's branch of the same name too: its run is a fork's CI.
        found = [
            ("branch", r)
            for r in (await self._api(repo, runs, fields={"branch": branch, "per_page": 20})).get(
                "workflow_runs"
            )
            or []
            if _this_repos(r)
        ]
        if ref := await self.find_mr(repo=repo, branch=branch):
            prefix = _QUEUE_BRANCH.format(base=base, number=ref.number)
            group = await self._api(repo, runs, fields={"event": "merge_group", "per_page": 100})
            found += [
                ("merge_group", r)
                for r in group.get("workflow_runs") or []
                if str(r.get("head_branch") or "").startswith(prefix) and _this_repos(r)
            ][:20]
        rows = [
            Pipeline(
                id=str(r.get("id")),
                kind=kind,  # type: ignore[arg-type]
                ref=str(r.get("head_branch") or ""),
                sha=str(r.get("head_sha") or ""),
                status=str(
                    r.get("conclusion") if r.get("status") == "completed" else r.get("status")
                ),
                url=str(r.get("html_url") or ""),
                created_at=str(r.get("created_at") or ""),
            )
            for kind, r in found
        ]
        return tuple(sorted(rows, key=lambda p: p.created_at, reverse=True))

    async def jobs(self, *, repo: Path, pipeline_id: str) -> tuple[Job, ...]:
        rows = await self._paged(
            repo, f"repos/{{owner}}/{{repo}}/actions/runs/{pipeline_id}/jobs", key="jobs"
        )
        return tuple(
            Job(
                id=str(j.get("id")),
                name=str(j.get("name") or ""),
                stage="",
                status=str(
                    j.get("conclusion") if j.get("status") == "completed" else j.get("status")
                ),
                failure_reason=(
                    str(j["conclusion"])
                    if str(j.get("conclusion") or "").upper() in _GH_RED
                    else None
                ),
                url=str(j.get("html_url") or ""),
            )
            for j in rows
        )

    async def job_log(self, *, repo: Path, job_id: str) -> str:
        resp = await self._transport(repo).rest(
            "GET", f"repos/{{owner}}/{{repo}}/actions/jobs/{job_id}/logs", tail=True, download=True
        )
        return resp.text

    async def merged_mrs(self, *, repo: Path, base: str, limit: int) -> tuple[MergedMR, ...]:
        pulls = await self._api(
            repo,
            "repos/{owner}/{repo}/pulls",
            fields={
                "state": "closed",
                "base": base,
                "sort": "updated",
                "direction": "desc",
                "per_page": min(100, limit * 3),
            },
        )
        merged = sorted(
            (p for p in pulls if p.get("merged_at")), key=lambda p: p["merged_at"], reverse=True
        )
        return tuple(
            MergedMR(
                number=int(p["number"]),
                title=str(p.get("title") or ""),
                labels=tuple(str(x.get("name")) for x in p.get("labels") or []),
                url=str(p.get("html_url") or ""),
                merged_at=str(p["merged_at"]),
            )
            for p in merged[:limit]
        )

    async def labels(self, *, repo: Path) -> tuple[Label, ...]:
        rows = await self._paged(repo, "repos/{owner}/{repo}/labels")
        return tuple(
            Label(str(r.get("name")), str(r.get("description") or ""), str(r.get("color") or ""))
            for r in rows
        )
