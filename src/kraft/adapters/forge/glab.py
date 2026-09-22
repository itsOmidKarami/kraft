"""GitLab through `glab`."""

from __future__ import annotations

from dataclasses import replace
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


#: `_failure_detail` returns this in place of an empty tuple whenever the
#: read itself failed (caught on review of this plan, the real form of
#: Kraft-h81i's exposure) -- `ForgeError` from `glab ci get`, a non-dict JSON
#: body, or no pipeline id to ask about at all. `failure_reason=None` is not
#: a member of `ci._INFRA_REASONS`, so `ci.is_infra_red` reads this as
#: code-red by default rather than as the forge's own zero-jobs case: a
#: settled `script_failure` we simply couldn't fetch detail for must not read
#: as infra just because the detail fetch itself came back empty.
_UNREADABLE_JOBS = (FailedJob("(unreadable)", "failed", None),)


class GlabCli(mr_ops.CliWaits):
    """GitLab through `glab`. Credentials stay in glab's own keyring."""

    async def open_mr(
        self,
        *,
        repo: Path,
        branch: str,
        base: str,
        title: str,
        body: str,
        meta: mr_ops.MRMeta | None = None,
    ) -> MR:
        await git.assert_clean(repo, base)
        # Both forges refuse to create against an unpushed branch. `--fill --yes`
        # would push too, but pushing explicitly keeps the failure legible when
        # it is the push that fails rather than the create.
        await self.push(repo=repo, branch=branch)
        # Not `--fill`: it derives the title from the commits, and with more
        # than one commit glab falls back to the branch name — which for Kraft
        # is always the work item id, so every MR read as a hex string
        # (Kraft-c09h).
        await git.run_git(
            repo,
            [
                "glab",
                "mr",
                "create",
                # Every MR Kraft opens starts as a draft: `human_review` is
                # the gate that decides it's ready, not this call (draft-MR
                # workflow spec). `mr_sync` un-drafts it after the gate.
                "--draft",
                "--target-branch",
                base,
                "--title",
                mr_ops.mr_title(title),
                "--description",
                body,
                *mr_ops.meta_flags(meta or mr_ops.MRMeta()),
                "--yes",
            ],
        )
        # Read the MR back rather than parsing create's human-formatted output.
        raw = await git.run_git(repo, ["glab", "mr", "view", "-F", "json"])
        data = mr_ops.parse_json(raw, "glab mr view")
        return MR(number=int(data["iid"]), url=str(data["web_url"]))

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        """Un-draft the merge request. `--ready` is a no-op on an
        already-ready MR (glab's own docs), so this is safe to call
        unconditionally rather than reading the MR back first to check."""
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["glab", "mr", "update", *target, "--ready"])

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        # No iid: `glab mr update` resolves the merge request from the
        # checked-out branch, the way merge and ci already do.
        await git.run_git(repo, ["glab", "mr", "update", "--description", body])

    async def _merge_state(self, repo: Path, branch: str = "") -> str:
        """The merge request's own view of whether it can merge.

        `glab mr view -F json` is the call `open_mr` already makes, resolved
        from the checked-out branch. `detailed_merge_status` says *why*;
        `merge_status` is the older, coarser field, kept as a fallback for an
        older GitLab. Anything else — including a payload that is not an object
        — is "the forge did not say", which `mr.mergeable` reads as undecided.

        A merge request already merged out-of-band -- a person merges it in
        the GitLab UI while this node is still polling -- reads `"state":
        "merged"` here, checked ahead of either merge-status field: a merged
        MR may not carry either (Kraft-v6ci). And if `glab mr view` itself
        raises -- the reported failure shape, the branch resolving to nothing
        when GitLab is asked for its current view -- fall back to `find_mr`,
        the same `--all` lookup `merge`'s own "already merged" shortcut
        already trusts (Kraft-xron), before giving up: this only reads as
        "merged" when `find_mr` confirms it, so a genuine outage or auth
        failure still raises rather than being read as "someone merged it".
        """
        try:
            raw = await git.run_git(repo, ["glab", "mr", "view", "-F", "json"])
            data = mr_ops.parse_json(raw, "glab mr view")
        except ForgeError:
            ref = await self.find_mr(repo=repo, branch=branch) if branch else None
            if ref is not None and ref.state == "merged":
                return "merged"
            raise
        if not isinstance(data, dict):
            return ""
        if data.get("state") == "merged":
            return "merged"
        return str(data.get("detailed_merge_status") or data.get("merge_status") or "")

    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        # The merge request's own state first: a conflict fails the check node
        # whatever colour the pipeline is, and no wait should sit out a
        # pipeline to learn it (Kraft-ejj9). Only meaningful pre-merge, on the
        # checked-out feature branch that actually has an open MR -- see
        # `branch_ci_status` for the post-merge, no-MR read.
        detail = await self._merge_state(repo, branch)
        # Merged out-of-band is a result, not a conflict and not a pipeline to
        # read: a deleted source branch's pipeline list is not a fact this
        # node needs once the MR has already landed (Kraft-v6ci). `_run_one`'s
        # `case "ci_poll"` already treats state == "success" as status ==
        # "done" -- no handler-side change needed there.
        if detail == "merged":
            return CIStatus(
                state="success",
                url="",
                jobs=("merge request already merged",),
                mergeable=True,
                merge_detail="merged",
            )
        mergeable = mr_ops.mergeable(detail)
        block_reason = mr_ops.classify_block_reason(detail)
        expected_sha = await git._head_sha(repo)
        pipeline = await self._pipeline_on_ref(repo, branch, pipeline_id, expected_sha)
        return replace(
            pipeline, mergeable=mergeable, merge_detail=detail, block_reason=block_reason
        )

    async def branch_ci_status(
        self, *, repo: Path, branch: str, head_sha: str, pipeline_id: str = ""
    ) -> CIStatus:
        """The target branch's own pipeline, not a merge request's.

        `merge_watch` calls this once the checked-out branch's MR is already
        merged: `ci_status`'s `_merge_state` resolves from the checked-out
        branch, which normally has no open MR left to find, and would raise
        instead of reading the branch's pipeline at all. `mergeable` is a
        merge-request question with no post-merge meaning, so it stays the
        default `None` here rather than going through `_merge_state`.
        `head_sha` is the caller's own freshly-fetched upstream head, not this
        checkout's local HEAD, which may not be pulled -- see
        `_pipeline_on_ref`.
        """
        return await self._pipeline_on_ref(repo, branch, pipeline_id, head_sha)

    async def _pipeline_on_ref(
        self, repo: Path, branch: str, pipeline_id: str, expected_sha: str
    ) -> CIStatus:
        """Read the branch's pipeline, pinned or latest, with no merge-request
        involvement at all -- shared by `ci_status` and `branch_ci_status`,
        which differ only in whether `mergeable`/`merge_detail` get filled in
        on top of what this returns.
        """
        if pipeline_id:
            # Pinned to a pipeline the caller already resolved against the
            # current head (Kraft-ivh1): no sha guard needed here, unlike
            # `glab ci list` below, which can answer with the previous
            # commit's pipeline for a few seconds after a push.
            try:
                raw = await git.run_git(
                    repo, ["glab", "ci", "get", "--pipeline-id", pipeline_id, "-F", "json"]
                )
                top = mr_ops.parse_json(raw, "glab ci get")
            except ForgeError:
                top = None
            if isinstance(top, dict) and top:
                raw_state = str(top.get("status", ""))
                state: CIState = _GLAB_STATES.get(raw_state, "failed")
                url = str(top.get("web_url", ""))
                sha = str(top.get("sha", ""))
                pipeline_ref = str(top.get("id", pipeline_id))
                jobs: tuple[str, ...] = (f"pipeline {pipeline_ref}: {raw_state}",)
                failed_jobs: tuple[FailedJob, ...] = ()
                if state == "failed":
                    detail_lines, failed_jobs = await self._failure_detail(repo, pipeline_ref)
                    jobs += detail_lines
                return CIStatus(
                    state=state,
                    url=url,
                    jobs=jobs,
                    sha=sha,
                    failed_jobs=failed_jobs,
                    pipeline_ref=pipeline_ref,
                )
            # Pinned id unreadable (deleted, transient CLI error) -- fall
            # through to the ordinary "resolve latest on branch" path below
            # rather than erroring the whole poll.
        # --ref, or this returns the newest pipeline in the whole project: a
        # green run on main would pass the gate for a red branch.
        ref = ["--ref", branch] if branch else []
        raw = await git.run_git(repo, ["glab", "ci", "list", "-F", "json", "-P", "1", *ref])
        rows = mr_ops.parse_json(raw, "glab ci list")
        # No pipeline yet is not a green one.
        state = "pending"
        url, jobs, sha, pipeline_ref = "", ("no pipeline yet",), "", ""
        failed_jobs = ()
        if rows:
            top = rows[0]
            # For a few seconds after a push, this list still answers with the
            # *previous* commit's pipeline — green, for code the branch no
            # longer has. ci_poll pushes now (Kraft-bxj8), so that window is on
            # the hot path, and a pipeline that is not for this head is not a
            # result. Same answer as no pipeline at all: pending. Guarded
            # against `expected_sha`, the caller's own reference commit --
            # `ci_status` passes this checkout's local HEAD (correct: it is
            # the branch being checked), `branch_ci_status` passes the
            # upstream head it fetched, never this checkout's possibly-stale
            # local HEAD (plan-review finding, second half).
            sha = str(top.get("sha", ""))
            if sha and expected_sha and sha != expected_sha:
                jobs = (f"no pipeline for {expected_sha[:7]} yet",)
            else:
                raw_state = str(top.get("status", ""))
                state = _GLAB_STATES.get(raw_state, "failed")
                url = str(top.get("web_url", ""))
                pipeline_ref = str(top.get("id", ""))
                jobs = (f"pipeline {top.get('id')}: {raw_state}",)
                if state == "failed":
                    detail_lines, failed_jobs = await self._failure_detail(repo, pipeline_ref)
                    jobs += detail_lines
        return CIStatus(
            state=state,
            url=url,
            jobs=jobs,
            sha=sha,
            failed_jobs=failed_jobs,
            pipeline_ref=pipeline_ref,
        )

    async def _failure_detail(
        self, repo: Path, pipeline_id: str
    ) -> tuple[tuple[str, ...], tuple[FailedJob, ...]]:
        """The failed jobs of a red pipeline, each job's own `failure_reason`
        (Kraft-ddxn), and the trace tail for each -- MR !89 was red because a
        job wanted a `release::` label, and that sentence existed only in the
        job's trace (Kraft-xh0q). A pipeline with no jobs at all reports the
        pipeline's own `yaml_errors` instead (Kraft-h81i, Kraft-s8ul,
        Kraft-ddxn): a config error stops anything from being created, so
        there is no job to ask.

        Best effort throughout: a diagnosis that cannot be fetched must not
        turn a pipeline result that *was* read into a node failure. But
        "could not fetch the diagnosis" and "the forge confirmed there is no
        diagnosis to fetch" are not the same fact, and returning the same
        `((), ())` for both was a real bug caught on review of this plan: a
        genuine `script_failure` whose `glab ci get` call happens to fail (a
        transient CLI error, a malformed response) was indistinguishable from
        a pipeline the forge itself reports as having zero jobs, and
        `ci.is_infra_red` reads an empty `failed_jobs` tuple as infra-shaped
        either way -- so an ordinary code failure would self-retry twice and
        stop at `needs_human` with no fix loop ever run, the exact outcome
        this spec exists to remove. The three "could not read" branches below
        (`pipeline_id` missing, `ForgeError`, unparseable JSON) return
        `_UNREADABLE_JOBS` instead of `()`; only a *successful* read that
        confirms no job actually ran (empty `jobs`, no `yaml_errors`) returns
        the real empty tuple `is_infra_red` treats as infra.
        """
        if not pipeline_id:
            return (), _UNREADABLE_JOBS
        try:
            raw = await git.run_git(
                repo, ["glab", "ci", "get", "--pipeline-id", pipeline_id, "-F", "json"]
            )
            data = mr_ops.parse_json(raw, "glab ci get")
        except ForgeError:
            return (), _UNREADABLE_JOBS
        if not isinstance(data, dict):
            return (), _UNREADABLE_JOBS
        jobs_raw = [j for j in (data.get("jobs") or []) if isinstance(j, dict)]
        failed_raw = [j for j in jobs_raw if str(j.get("status", "")) == "failed"]
        if not failed_raw:
            yaml_errors = data.get("yaml_errors")
            if yaml_errors:
                return (f"pipeline config error: {yaml_errors}",), (
                    FailedJob("(pipeline)", "failed", "config_error"),
                )
            return (), ()
        detail: tuple[str, ...] = ()
        failed_jobs: list[FailedJob] = []
        # ponytail: first two failed jobs, last 15 trace lines each -- widen
        # if a real pipeline needs it (unchanged cap from before this task).
        for j in failed_raw[:2]:
            name = str(j.get("name", ""))
            reason = j.get("failure_reason")
            failed_jobs.append(FailedJob(name, "failed", str(reason) if reason else None))
            detail += (f"job {name}: failed" + (f" ({reason})" if reason else ""),)
            try:
                trace = await git.run_git(
                    repo, ["glab", "ci", "trace", name, "--pipeline-id", pipeline_id]
                )
            except ForgeError:
                continue
            detail += tuple(f"  {line}" for line in trace.strip().splitlines()[-15:])
        # Any failed job past the first two still counts for classification,
        # even though its trace is not fetched.
        failed_jobs += [
            FailedJob(
                str(j.get("name", "")),
                "failed",
                (str(j["failure_reason"]) if j.get("failure_reason") else None),
            )
            for j in failed_raw[2:]
        ]
        return detail, tuple(failed_jobs)

    async def _json(self, repo: Path, args: list[str], what: str):
        return mr_ops.parse_json(await git.run_git(repo, ["glab", *args]), what)

    async def _bot_review(self, repo: Path, bot: str) -> ReviewResult:
        """GitLab has no review state: `bot`'s unresolved discussions are
        actionable, one finding each; its approval is clean; neither yet is
        pending."""
        # ponytail: an approval is not tied to a head here -- a project that
        # keeps approvals across pushes lets an old approval settle a new head.
        mr = await self._json(repo, ["mr", "view", "-F", "json"], "glab mr view")
        iid = mr["iid"]
        discussions = await self._json(
            repo,
            ["api", f"projects/:id/merge_requests/{iid}/discussions?per_page=100"],
            "glab api discussions",
        )
        findings = tuple(
            f"{(n.get('position') or {}).get('new_path')}:"
            f"{(n.get('position') or {}).get('new_line')}: {n.get('body', '')}"
            if n.get("position")
            else str(n.get("body", ""))
            for d in discussions
            for n in (d.get("notes") or [])
            if mr_ops.same_login(str((n.get("author") or {}).get("username", "")), bot)
            and n.get("resolvable")
            and not n.get("resolved")
        )
        if findings:
            return ReviewResult("actionable", findings=findings, detail=f"{bot}: unresolved")
        approvals = await self._json(
            repo, ["api", f"projects/:id/merge_requests/{iid}/approvals"], "glab api approvals"
        )
        approved = any(
            mr_ops.same_login(str((a.get("user") or {}).get("username", "")), bot)
            for a in approvals.get("approved_by") or []
        )
        if approved:
            return ReviewResult("clean", detail=f"{bot}: approved")
        return ReviewResult("pending", detail=f"waiting for {bot} to review")

    async def _check_review(self, repo: Path, check: str) -> ReviewResult:
        """The commit status (a CI job or an external status) named `check`
        on the MR's head, its latest if it ran more than once."""
        mr = await self._json(repo, ["mr", "view", "-F", "json"], "glab mr view")
        head = mr["sha"]
        statuses = await self._json(
            repo,
            ["api", f"projects/:id/repository/commits/{head}/statuses?per_page=100"],
            "glab api statuses",
        )
        mine = sorted((s for s in statuses if s.get("name") == check), key=lambda s: s["id"])
        if not mine:
            return ReviewResult("pending", detail=f"waiting for {check} on {head[:7]}")
        status = str(mine[-1].get("status"))
        if status in ("success", "skipped"):
            return ReviewResult("clean", detail=f"{check}: {status}")
        if status in ("failed", "canceled"):
            return ReviewResult(
                "actionable",
                findings=(f"{check} {status}: {mine[-1].get('description') or ''}",),
            )
        return ReviewResult("pending", detail=f"{check}: {status}")

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        """Retries every failed/canceled job in the pipeline `ci` read.
        A no-op on GitLab's side if none are (docs), so calling this
        speculatively ahead of a re-poll (Kraft-h81i) is safe."""
        if not ci.pipeline_ref:
            return
        await git.run_git(
            repo, ["glab", "api", "-X", "POST", f"projects/:id/pipelines/{ci.pipeline_ref}/retry"]
        )

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the merge request, then start a pipeline that can see it.

        `CI_MERGE_REQUEST_LABELS` is fixed when a pipeline is created, so a job
        that failed for a missing label re-reads the old, empty value on retry.
        Labelling alone therefore leaves the merge request looking fixed and
        still red -- the re-create is part of the capability, not the caller's
        homework (Kraft-xh0q).

        GitLab's free tier does not enforce a scoped label's exclusivity
        server-side, so `--label` alone only adds: a second repair pass leaves
        both `release::minor` and `release::patch` on the MR, and
        `next_tag.py` now raises on more than one `release::` label rather
        than picking one (Kraft-zfdu8). So any current label sharing a new
        label's `scope::` prefix is dropped in the same call.
        """
        if not labels:
            return
        target = [str(mr.number)] if mr.number > 0 else []
        # Also the read that used to be sentinel-only (`mr.number <= 0`, to
        # resolve the number for the pipeline re-create below): every caller
        # now needs the MR's current labels too, so one `mr view` serves both.
        raw = await git.run_git(repo, ["glab", "mr", "view", *target, "-F", "json"])
        data = mr_ops.parse_json(raw, "glab mr view")
        number = mr.number if mr.number > 0 else int(data["iid"])
        current = [str(label) for label in data.get("labels") or []]
        scopes = {label.split("::", 1)[0] + "::" for label in labels if "::" in label}
        drop = [
            label
            for label in current
            if label not in labels and any(label.startswith(scope) for scope in scopes)
        ]
        args = ["glab", "mr", "update", *target, "--label", ",".join(labels)]
        for label in drop:
            args += ["--unlabel", label]
        await git.run_git(repo, args)
        # `projects/:id` is glab's own placeholder for the repo the command is
        # run in, so this stays as repo-agnostic as every other call here.
        await git.run_git(
            repo,
            ["glab", "api", "-X", "POST", f"projects/:id/merge_requests/{number}/pipelines"],
        )

    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None:
        await git._assert_pushed(repo, branch)
        # number 0 is "not known": `run_task` does not thread the MR between
        # nodes, and both CLIs resolve it from the checked-out branch. Passing
        # a literal 0 would target a merge request that does not exist.
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["glab", "mr", "merge", *target, "--yes"])

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        # --all, or a merged merge request reads as no merge request at all.
        raw = await git.run_git(
            repo,
            ["glab", "mr", "list", "--all", "--source-branch", branch, "-F", "json", "-P", "5"],
        )
        rows = mr_ops.parse_json(raw, "glab mr list")
        return mr_ops.pick_mr(
            [
                MRRef(
                    number=int(r["iid"]),
                    url=str(r.get("web_url", "")),
                    state=_GLAB_MR_STATES.get(str(r.get("state", "")), "closed"),
                    merge_queued=bool(r.get("merge_when_pipeline_succeeds")),
                )
                for r in rows
            ]
        )
