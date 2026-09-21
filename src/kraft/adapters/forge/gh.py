"""GitHub through `gh`."""

from __future__ import annotations

import re
from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import (
    MR,
    CIState,
    CIStatus,
    FailedJob,
    ForgeError,
    MRRef,
    ReviewResult,
)

_GH_MR_STATES: dict[str, str] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}

#: gh's coarser conclusion vocabulary, mapped onto GitLab's `failure_reason`
#: names so `ci.is_infra_red` reads one vocabulary regardless of backend
#: (ponytail: a flat dict, not a shared enum -- add a name here if gh starts
#: distinguishing more failure shapes).
_GH_FAILURE_REASON = {
    "TIMED_OUT": "job_execution_timeout",
    "STARTUP_FAILURE": "runner_system_failure",
    "CANCELLED": "cancelled",
    "FAILURE": "script_failure",
    "ACTION_REQUIRED": "script_failure",
}
_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")


def _latest_by_name(rows: list[dict], *, name: str, started: str) -> list[dict]:
    """Collapse to one row per check name, keeping the one with the latest
    `started` timestamp.

    `test.yml` deliberately re-triggers on `labeled`/`unlabeled` (release-impact
    needs to see label changes), so a label Kraft sets mid-check starts a
    second workflow run on the same PR. `statusCheckRollup` and `gh run list`
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

    async def open_mr(
        self,
        *,
        repo: Path,
        branch: str,
        title: str,
        body: str,
        meta: mr_ops.MRMeta | None = None,
    ) -> MR:
        await git.assert_clean(repo)
        await self.push(repo=repo, branch=branch)
        # `--fill` titles the PR from the commits; see GlabCli.open_mr.
        await git.run_git(
            repo,
            [
                "gh",
                "pr",
                "create",
                # See GlabCli.open_mr.
                "--draft",
                "--title",
                mr_ops.mr_title(title),
                "--body",
                body,
                *mr_ops.meta_flags(meta or mr_ops.MRMeta()),
            ],
        )
        raw = await git.run_git(repo, ["gh", "pr", "view", "--json", "number,url"])
        data = mr_ops.parse_json(raw, "gh pr view")
        return MR(number=int(data["number"]), url=str(data["url"]))

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        """`gh pr ready` is a no-op on an already-ready PR (gh's own docs),
        same reasoning as `GlabCli.mark_ready`."""
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["gh", "pr", "ready", *target])

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        await git.run_git(repo, ["gh", "pr", "edit", "--body", body])

    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        # `gh pr view` with no argument already resolves from the current
        # branch, so `branch` is accepted for one Forge shape and unused here.
        # `pipeline_id` is a GitLab-only concept (Kraft-ivh1): GitHub's checks
        # are per-PR-head, not per-pipeline, so there is nothing to pin to and
        # this is accepted only for signature symmetry.
        # `mergeable,mergeStateStatus` ride along on the call the node already
        # makes: the check node has to know whether the PR can land, and one
        # round trip already carries it (Kraft-ejj9).
        raw = await git.run_git(
            repo,
            [
                "gh",
                "pr",
                "view",
                "--json",
                "number,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,headRefOid",
            ],
        )
        data = mr_ops.parse_json(raw, "gh pr view")
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
        # is the one the checks' runs belong to, or "" when that can't be told.
        sha = await self._checks_sha(repo, data.get("statusCheckRollup") or [])
        # gh's "no checks yet" (empty statusCheckRollup) stays "pending",
        # unchanged -- that is a precursor state (checks not yet registered),
        # not GitLab's *settled*-red-with-zero-jobs case Kraft-ddxn is about,
        # so it is not treated as infra here.
        checks = _latest_by_name(
            data.get("statusCheckRollup") or [], name="name", started="startedAt"
        )
        if not checks:
            return CIStatus(
                state="pending",
                url=str(data.get("url", "")),
                jobs=("no checks yet",),
                mergeable=mergeable,
                merge_detail=detail,
                block_reason=block_reason,
                sha=sha,
            )
        jobs = tuple(f"{c.get('name')}: {c.get('conclusion') or 'PENDING'}" for c in checks)
        conclusions = [str(c.get("conclusion") or "") for c in checks]
        if any(
            c in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
            for c in conclusions
        ):
            state: CIState = "failed"
        elif all(c in ("SUCCESS", "NEUTRAL", "SKIPPED") for c in conclusions):
            state = "success"
        else:
            state = "pending"
        failed_jobs = tuple(
            FailedJob(
                str(c.get("name", "")),
                "failed",
                _GH_FAILURE_REASON.get(str(c.get("conclusion") or "")),
                c.get("detailsUrl"),
            )
            for c in checks
            if str(c.get("conclusion") or "")
            in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
        )
        return CIStatus(
            state=state,
            url=str(data.get("url", "")),
            jobs=jobs,
            mergeable=mergeable,
            merge_detail=detail,
            block_reason=block_reason,
            sha=sha,
            failed_jobs=failed_jobs,
        )

    async def _json(self, repo: Path, args: list[str], what: str):
        return mr_ops.parse_json(await git.run_git(repo, ["gh", *args]), what)

    async def _bot_review(self, repo: Path, bot: str) -> ReviewResult:
        """`bot`'s latest review of the PR's current head, dismissed ones
        skipped: pending until there is one. Changes requested, or any inline comment, is actionable
        -- one finding per comment; anything else is clean."""
        pr = await self._json(repo, ["pr", "view", "--json", "number,headRefOid"], "gh pr view")
        number, head = pr["number"], pr["headRefOid"]
        # ponytail: one page of 100 reviews -- paginate if a PR ever has more.
        reviews = await self._json(
            repo,
            ["api", f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews?per_page=100"],
            "gh api reviews",
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
        comments = await self._json(
            repo,
            [
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews/{last['id']}/comments?per_page=100",
            ],
            "gh api review comments",
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

    async def _check_review(self, repo: Path, check: str) -> ReviewResult:
        """The check run `check` on the PR's head, or else the commit status
        of that name: pending until it completes, clean on success, and
        actionable on anything else, its output the finding."""
        pr = await self._json(repo, ["pr", "view", "--json", "number,headRefOid"], "gh pr view")
        head = pr["headRefOid"]
        runs = (
            await self._json(
                repo,
                ["api", f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs?check_name={check}"],
                "gh api check-runs",
            )
        ).get("check_runs") or []
        if runs:
            run = runs[0]
            if run.get("status") != "completed":
                return ReviewResult("pending", detail=f"{check}: {run.get('status')}")
            conclusion = str(run.get("conclusion") or "")
            if conclusion in ("success", "neutral", "skipped"):
                return ReviewResult("clean", detail=f"{check}: {conclusion}")
            output = run.get("output") or {}
            text = "\n".join(str(output[k]) for k in ("title", "summary", "text") if output.get(k))
            return ReviewResult("actionable", findings=(f"{check} {conclusion}: {text}",))
        statuses = (
            await self._json(
                repo, ["api", f"repos/{{owner}}/{{repo}}/commits/{head}/status"], "gh api status"
            )
        ).get("statuses") or []
        status = next((s for s in statuses if s.get("context") == check), None)
        if status is None or status.get("state") == "pending":
            return ReviewResult("pending", detail=f"waiting for {check} on {head[:7]}")
        if status.get("state") == "success":
            return ReviewResult("clean", detail=f"{check}: success")
        return ReviewResult(
            "actionable",
            findings=(f"{check} {status.get('state')}: {status.get('description') or ''}",),
        )

    async def _checks_sha(self, repo: Path, rollup: list[dict]) -> str:
        """The single head sha the rollup's Actions runs were built from.

        `statusCheckRollup` entries carry no sha, only a `detailsUrl` naming the
        run, so `gh run list` maps run id to `headSha`. `""` -- the guard stays
        inert -- when the checks span shas, name no run, or the lookup fails.
        """
        ids = {
            m.group(1) for c in rollup if (m := _RUN_ID_RE.search(str(c.get("detailsUrl") or "")))
        }
        if not ids:
            return ""
        try:
            raw = await git.run_git(
                repo, ["gh", "run", "list", "-L", "100", "--json", "databaseId,headSha"]
            )
            rows = mr_ops.parse_json(raw, "gh run list")
        except ForgeError:
            return ""
        if not isinstance(rows, list):
            return ""
        shas = {
            str(r.get("headSha") or "")
            for r in rows
            if isinstance(r, dict) and str(r.get("databaseId")) in ids
        }
        return shas.pop() if len(shas) == 1 else ""

    async def branch_ci_status(
        self, *, repo: Path, branch: str, head_sha: str, pipeline_id: str = ""
    ) -> CIStatus:
        """The target branch's own CI, not a pull request's.

        `merge_watch` calls this once the merge has landed: the checkout's
        current branch normally has no open PR left for `ci_status`'s `gh pr
        view` to resolve, and it would raise instead of reading the branch's
        runs at all. `gh run list --branch` reads the branch's own workflow
        runs directly, no PR involved. `pipeline_id` is unused, same reason
        as in `ci_status`: gh's checks are per-commit, not per-pipeline.
        `head_sha` is the caller's own freshly-fetched upstream head, not this
        checkout's possibly-stale local HEAD, so it is what runs are matched
        against here.
        """
        raw = await git.run_git(
            repo,
            [
                "gh",
                "run",
                "list",
                "--branch",
                branch,
                "-L",
                "20",
                "--json",
                "status,conclusion,headSha,url,name",
            ],
        )
        rows = mr_ops.parse_json(raw, "gh run list")
        runs = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        current = [r for r in runs if str(r.get("headSha") or "") == head_sha]
        if not current:
            return CIStatus(state="pending", url="", jobs=(f"no run for {head_sha[:7]} yet",))
        jobs = tuple(
            f"{r.get('name')}: {r.get('conclusion') or r.get('status') or 'PENDING'}"
            for r in current
        )
        if any(str(r.get("status") or "") != "completed" for r in current):
            state: CIState = "pending"
        else:
            conclusions = [str(r.get("conclusion") or "") for r in current]
            if any(
                c in ("failure", "timed_out", "cancelled", "action_required", "startup_failure")
                for c in conclusions
            ):
                state = "failed"
            elif all(c in ("success", "neutral", "skipped") for c in conclusions):
                state = "success"
            else:
                state = "pending"
        failed_jobs = tuple(
            FailedJob(
                str(r.get("name", "")),
                "failed",
                _GH_FAILURE_REASON.get(str(r.get("conclusion") or "").upper()),
                str(r.get("url") or "") or None,
            )
            for r in current
            if str(r.get("conclusion") or "")
            in ("failure", "timed_out", "cancelled", "action_required", "startup_failure")
        )
        return CIStatus(
            state=state,
            url=str(current[0].get("url", "")),
            jobs=jobs,
            sha=head_sha,
            failed_jobs=failed_jobs,
        )

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        """`gh run rerun <id> --failed` for every distinct Actions run behind
        `ci`'s failed checks. Best-effort: a check with no `/actions/runs/<id>/`
        in its `detailsUrl` (a non-Actions status check) is skipped."""
        run_ids = {
            m.group(1)
            for job in ci.failed_jobs
            if job.detail_url and (m := _RUN_ID_RE.search(job.detail_url))
        }
        for run_id in run_ids:
            await git.run_git(repo, ["gh", "run", "rerun", run_id, "--failed"])

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the pull request.

        No pipeline to re-create, unlike GitLab: a workflow that cares about
        labels keys on `pull_request: types: [labeled]` and GitHub re-evaluates
        it on the edit.
        """
        if not labels:
            return
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["gh", "pr", "edit", *target, "--add-label", ",".join(labels)])

    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None:
        await git._assert_pushed(repo, branch)
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["gh", "pr", "merge", *target, "--squash"])

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        raw = await git.run_git(
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
        rows = mr_ops.parse_json(raw, "gh pr list")
        return mr_ops.pick_mr(
            [
                MRRef(
                    number=int(r["number"]),
                    url=str(r.get("url", "")),
                    state=_GH_MR_STATES.get(str(r.get("state", "")), "closed"),
                )
                for r in rows
            ]
        )
