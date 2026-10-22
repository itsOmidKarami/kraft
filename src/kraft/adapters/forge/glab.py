"""GitLab through `glab`."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import (
    MR,
    CIState,
    CIStatus,
    DropCause,
    FailedJob,
    ForgeError,
    HeadMoved,
    MRRef,
    QueueRead,
    QueueState,
    ReviewResult,
)

#: glab's pipeline vocabulary, from `glab ci list --help` (glab 1.116.0).
#: 'skipped' is deliberately not success: nothing proved the branch green, and
#: the next node is merge. Anything unrecognised falls through to 'failed' for
#: the same reason — guessing in the direction of merging is the one guess that
#: cannot be walked back. 'canceled' is a wait, not red: a cancelled pipeline
#: is never a verdict (Kraft-zn8me). `set_labels` re-creates the MR pipeline
#: and auto-cancel of redundant pipelines cancels the old one; a pipeline a
#: person cancelled with no successor is `ci.render_ci`'s "abandoned".
_GLAB_STATES: dict[str, CIState] = {
    "success": "success",
    "failed": "failed",
    "canceled": "pending",
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


#: Where a merged results pipeline runs: a merge commit of the merge request's
#: head into its target, built afresh on every push. Its `sha` is that merge
#: commit, never the head, so the head it tested is the commit's second parent
#: (`_answered_for`).
_MERGE_REF = "refs/merge-requests/{iid}/merge"

#: Where a merge train builds its pipeline: the merge request's head merged
#: into the target and everything ahead of it on the train. A drop for a failed
#: train pipeline leaves it as the merge request's `head_pipeline`.
_TRAIN_REF = "refs/merge-requests/{iid}/train"

#: A merge train car's `status` (GitLab `MergeTrains::Car`), as `QueueRead`
#: states. An unrecognised status is a car that still exists, so still queued.
_TRAIN_CAR_STATES: dict[str, QueueState] = {
    "idle": "queued",
    "stale": "queued",
    "fresh": "queued",
    "merging": "merging",
    "merged": "merged",
    "skip_merged": "merged",
}

#: The system notes `SystemNotes::MergeTrainService` writes when a merge
#: request leaves a train without merging: removed from it (by a person when
#: no reason follows), never added to it, or a person cancelling the add.
_REMOVED_NOTE = "removed this merge request from the merge train"
_ABORTED_NOTE = "aborted automatic add to merge train because "
_CANCELLED_ADD_NOTE = "cancelled automatic add to merge train"
#: The ones it writes when a merge request joins a train, or is set to join
#: one once its pipeline succeeds. A leaving note older than these belongs to
#: an earlier entry, not to this one.
_ENQUEUE_NOTES = (
    "started a merge train",
    "added this merge request to the merge train",
    "enabled automatic add to merge train",
)


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
        check_clean: bool = True,
    ) -> MR:
        if check_clean:
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
                # workflow spec). `mark_ready` un-drafts it after the gate.
                "--draft",
                "--source-branch",
                branch,
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
        raw = await git.run_git(repo, ["glab", "mr", "view", branch, "-F", "json"])
        data = mr_ops.parse_json(raw, "glab mr view")
        return MR(number=int(data["iid"]), url=str(data["web_url"]))

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        """Un-draft the merge request. `--ready` is a no-op on an
        already-ready MR (glab's own docs), so this is safe to call
        unconditionally rather than reading the MR back first to check."""
        target = mr_ops.target(mr.number, branch)
        await git.run_git(repo, ["glab", "mr", "update", *target, "--ready"])

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        await git.run_git(repo, ["glab", "mr", "update", branch, "--description", body])

    async def mr_body(self, *, repo: Path, branch: str) -> str:
        data = await self._json(repo, ["mr", "view", branch, "-F", "json"], "glab mr view")
        return str(data.get("description") or "")

    async def _merge_state(self, repo: Path, branch: str = "") -> str:
        """The merge request's own view of whether it can merge (`_merge_view`)."""
        return (await self._merge_view(repo, branch))[0]

    async def _merge_view(self, repo: Path, branch: str = "") -> tuple[str, dict]:
        """The merge request's own view of whether it can merge, and the
        `glab mr view` object it came from (`{}` when there is none to give:
        a payload that is not an object, or the `find_mr` fallback below).
        `ci_status` reads the object's `head_pipeline` and `iid` too, so the
        merge request is asked once per read.

        `glab mr view <branch> -F json` is the call `open_mr` already makes.
        `detailed_merge_status` says *why*; `merge_status` is the older,
        coarser field, kept as a fallback for an older GitLab. Anything else
        — including a payload that is not an object — is "the forge did not
        say", which `mr.mergeable` reads as undecided.

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
            raw = await git.run_git(
                repo, ["glab", "mr", "view", *mr_ops.target(0, branch), "-F", "json"]
            )
            data = mr_ops.parse_json(raw, "glab mr view")
        except ForgeError:
            ref = await self.find_mr(repo=repo, branch=branch) if branch else None
            if ref is not None and ref.state == "merged":
                return "merged", {}
            raise
        if not isinstance(data, dict):
            return "", {}
        if data.get("state") == "merged":
            return "merged", data
        return str(data.get("detailed_merge_status") or data.get("merge_status") or ""), data

    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        # The merge request's own state first: a conflict fails the check node
        # whatever colour the pipeline is, and no wait should sit out a
        # pipeline to learn it (Kraft-ejj9). Only meaningful pre-merge, on the
        # checked-out feature branch that actually has an open MR -- see
        # `branch_ci_status` for the post-merge, no-MR read.
        detail, view = await self._merge_view(repo, branch)
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
        expected_sha = await git._head_sha(repo, branch)
        pipeline = await self._pipeline_on_ref(repo, branch, pipeline_id, expected_sha, view)
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
        self,
        repo: Path,
        branch: str,
        pipeline_id: str,
        expected_sha: str,
        view: dict | None = None,
    ) -> CIStatus:
        """Read the newest pipeline for `expected_sha`, pinned or not --
        shared by `ci_status` and `branch_ci_status`, which differ only in
        whether `mergeable`/`merge_detail` get filled in on top of what this
        returns. `view` is `ci_status`'s merge request (`_merge_view`): its
        `head_pipeline` is a candidate, and its `iid` lets a merged results
        pipeline count (`_answered_for`). `branch_ci_status` has none, so no
        merge request's pipeline ever answers for a target branch.
        """
        view = view or {}
        iid = str(view.get("iid") or "")
        if pipeline_id:
            # Pinned to a pipeline the caller already resolved against the
            # current head (Kraft-ivh1): no re-resolving here, unlike
            # `glab ci list` below, which can answer with the previous
            # commit's pipeline for a few seconds after a push.
            try:
                raw = await git.run_git(
                    repo, ["glab", "ci", "get", "--pipeline-id", pipeline_id, "-F", "json"]
                )
                top = mr_ops.parse_json(raw, "glab ci get")
            except ForgeError:
                top = None
            # A cancelled pin has a successor to find, so it falls through too.
            # So does one this head no longer answers for: returning it would
            # have `ci_poll` pin it again on every poll.
            answered = None
            if isinstance(top, dict) and top and top.get("status") != "canceled":
                answered, _ = await self._answered_for(repo, top, expected_sha, iid)
            if isinstance(top, dict) and answered is not None:
                raw_state = str(top.get("status", ""))
                state: CIState = _GLAB_STATES.get(raw_state, "failed")
                url = str(top.get("web_url", ""))
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
                    sha=answered,
                    failed_jobs=failed_jobs,
                    pipeline_ref=pipeline_ref,
                )
            # Pinned id unreadable (deleted, transient CLI error) -- fall
            # through to the ordinary "newest for this head" path below
            # rather than erroring the whole poll.
        # --ref, or this returns the newest pipeline in the whole project: a
        # green run on main would pass the gate for a red branch.
        ref = ["--ref", branch] if branch else []
        raw = await git.run_git(repo, ["glab", "ci", "list", "-F", "json", "-P", "1", *ref])
        rows = mr_ops.parse_json(raw, "glab ci list")
        row = rows[0] if rows and isinstance(rows[0], dict) else None
        # Two candidates, the merge request's head pipeline first so the list
        # row wins a tie. Both, because neither is enough alone: GitLab moves
        # `head_pipeline` in a background worker after a pipeline is created,
        # so right after `set_labels` or a description re-run starts one it can
        # still name the old red pipeline for this same head, which the list
        # (gitlab.com's `?ref=` includes merge request pipelines) already has
        # a newer one than; and a GitLab whose `?ref=` leaves merge request
        # pipelines out of the list has only the payload to offer.
        # shortcut: on such a GitLab, that lag can still hand back the old
        # pipeline for a few seconds after a re-create; `set_labels` could
        # return the id it created for the caller to pin instead.
        candidates: dict[str, dict] = {}
        head_pipeline = view.get("head_pipeline")
        if isinstance(head_pipeline, dict) and head_pipeline.get("id"):
            candidates[str(head_pipeline["id"])] = head_pipeline
        if row is not None:
            candidates[str(row.get("id", ""))] = row
        accepted: list[tuple[dict, str]] = []
        refused: list[str] = []
        for top in candidates.values():
            answered, why = await self._answered_for(repo, top, expected_sha, iid)
            if answered is not None:
                accepted.append((top, answered))
            elif why:
                refused.append(why)
        if accepted:
            # The newest pipeline for the head, which is GitLab's own rule for
            # a merge request's head pipeline.
            top, answered = max(accepted, key=lambda pair: _pipeline_number(pair[0]))
            return await self._read_pipeline(repo, top, answered)
        # No pipeline yet is not a green one. For a few seconds after a push
        # the list still answers with the *previous* commit's pipeline -- green,
        # for code the branch no longer has. ci_poll pushes now (Kraft-bxj8), so
        # that window is on the hot path, and a pipeline that is not for this
        # head is not a result: the same answer as no pipeline at all.
        # `expected_sha` is the caller's own reference commit -- `ci_status`
        # passes the item branch's local tip, `branch_ci_status` the upstream
        # head it fetched, never this checkout's possibly-stale local HEAD.
        jobs = ("no pipeline yet",)
        if candidates:
            jobs = (f"no pipeline for {expected_sha[:7]} yet", *refused)
        return CIStatus(
            state="pending",
            url="",
            jobs=jobs,
            sha=str(row.get("sha", "")) if row is not None else "",
        )

    async def _read_pipeline(self, repo: Path, top: dict, sha: str) -> CIStatus:
        """One pipeline that answers for `sha` (`_answered_for`), as a
        `CIStatus`: `sha` is the head it tested, never a merge commit's own."""
        raw_state = str(top.get("status", ""))
        state: CIState = _GLAB_STATES.get(raw_state, "failed")
        # Never pin a cancelled pipeline: its successor is the one to read.
        pipeline_ref = "" if raw_state == "canceled" else str(top.get("id", ""))
        jobs: tuple[str, ...] = (f"pipeline {top.get('id')}: {raw_state}",)
        cancelled_at = ""
        if raw_state == "canceled":  # when, for `render_ci` (Kraft-kbqmk)
            cancelled_at = str(top.get("updated_at") or "")
        failed_jobs: tuple[FailedJob, ...] = ()
        if state == "failed":
            detail_lines, failed_jobs = await self._failure_detail(repo, pipeline_ref)
            jobs += detail_lines
        return CIStatus(
            state=state,
            url=str(top.get("web_url", "")),
            jobs=jobs,
            sha=sha,
            failed_jobs=failed_jobs,
            pipeline_ref=pipeline_ref,
            cancelled_at=cancelled_at,
        )

    async def _answered_for(
        self, repo: Path, top: dict, expected: str, iid: str
    ) -> tuple[str | None, str]:
        """The head pipeline `top` tested, when that is `expected`, and why
        not otherwise.

        A pipeline on the head itself (a branch pipeline, a detached merge
        request pipeline) answers for its own `sha`; with no `sha` or no
        `expected` there is nothing to compare, and it answers for its own as
        it always has. A merged results pipeline's `sha` is a merge commit,
        built on `_MERGE_REF` for this merge request's `iid` only: it answers
        for `expected` when `expected` is that commit's second parent, the
        head merged in. Every merged results pipeline of one merge request
        shares that ref, and `head_pipeline` can still name the previous
        head's for a moment after a push, so the ref alone never proves it.
        `(None, why)` is "not for this head", a wait; `why` is "" when it is
        plainly another commit's, and names the pipeline when a merge commit
        was asked about, so a wait that times out says what it saw.
        """
        sha = str(top.get("sha") or "")
        if not sha or not expected or sha == expected:
            return sha, ""
        ref = str(top.get("ref") or "")
        if not iid or ref != _MERGE_REF.format(iid=iid):
            return None, ""
        # The merge ref lives in the pipeline's own project, which `:id` (the
        # checkout's remote) may not be.
        project = str(top.get("project_id") or ":id")
        name = f"pipeline {top.get('id')} on {ref}"
        try:
            raw = await git.run_git(
                repo, ["glab", "api", f"projects/{project}/repository/commits/{sha}"]
            )
            commit = mr_ops.parse_json(raw, "glab api commit")
        except ForgeError as exc:
            return None, f"{name}: could not read merge commit {sha[:7]}: {exc}"
        parents = commit.get("parent_ids") if isinstance(commit, dict) else None
        # The second parent; a slice, so a commit with one parent (or none)
        # reads as having tested nothing rather than raising.
        tested = (parents[1:2] or [""])[0] if isinstance(parents, list) else ""
        if tested != expected:
            return None, f"{name} tested {str(tested)[:7] or 'nothing'}, not {expected[:7]}"
        return expected, ""

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

    async def _bot_review(self, repo: Path, branch: str, bot: str) -> ReviewResult:
        """GitLab has no review state: `bot`'s unresolved discussions are
        actionable, one finding each; its approval is clean; neither yet is
        pending."""
        # ponytail: an approval is not tied to a head here -- a project that
        # keeps approvals across pushes lets an old approval settle a new head.
        mr = await self._json(repo, ["mr", "view", branch, "-F", "json"], "glab mr view")
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

    async def _check_review(self, repo: Path, branch: str, check: str) -> ReviewResult:
        """The commit status (a CI job or an external status) named `check`
        on the MR's head, its latest if it ran more than once. A merged
        results pipeline's jobs report on its merge commit, not the head, so
        that commit is read too when the merge request's head pipeline is one
        built from this head (`_answered_for`)."""
        mr = await self._json(repo, ["mr", "view", branch, "-F", "json"], "glab mr view")
        head = mr["sha"]
        # (project, commit) pairs: a merge commit lives in its pipeline's own
        # project, which `:id` (the checkout's remote) may not be.
        commits = [(":id", head)]
        head_pipeline = mr.get("head_pipeline")
        if isinstance(head_pipeline, dict) and head_pipeline.get("sha") not in (None, "", head):
            answered, _ = await self._answered_for(
                repo, head_pipeline, head, str(mr.get("iid") or "")
            )
            if answered == head:
                project = str(head_pipeline.get("project_id") or ":id")
                commits.append((project, str(head_pipeline["sha"])))
        statuses: list = []
        for project, sha in commits:
            got = await self._json(
                repo,
                ["api", f"projects/{project}/repository/commits/{sha}/statuses?per_page=100"],
                "glab api statuses",
            )
            statuses += got if isinstance(got, list) else []
        mine = sorted((s for s in statuses if s.get("name") == check), key=lambda s: s["id"])
        if not mine:
            return ReviewResult("pending", detail=f"waiting for {check} on {head[:7]}")
        status = str(mine[-1].get("status"))
        if status in ("success", "skipped"):
            return ReviewResult("clean", detail=f"{check}: {status}")
        if status == "failed":  # 'canceled' is never a verdict; it waits below
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

    async def set_labels(self, *, repo: Path, branch: str, mr: MR, labels: tuple[str, ...]) -> None:
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
        target = mr_ops.target(mr.number, branch)
        # Also the read that used to be sentinel-only (`mr.number <= 0`, to
        # resolve the number for the pipeline re-create below): every caller
        # now needs the MR's current labels too, so one `mr view` serves both.
        raw = await git.run_git(repo, ["glab", "mr", "view", *target, "-F", "json"])
        data = mr_ops.parse_json(raw, "glab mr view")
        number = mr.number if mr.number > 0 else int(data["iid"])
        current = [str(label) for label in data.get("labels") or []]
        drop = mr_ops.same_scope_labels(current, labels)
        args = ["glab", "mr", "update", *target, "--label", ",".join(labels)]
        for label in drop:
            args += ["--unlabel", label]
        await git.run_git(repo, args)
        await self._new_mr_pipeline(repo, number)

    async def _new_mr_pipeline(self, repo: Path, number: int) -> None:
        # `projects/:id` is glab's own placeholder for the repo the command is
        # run in, so this stays as repo-agnostic as every other call here.
        await git.run_git(
            repo,
            ["glab", "api", "-X", "POST", f"projects/:id/merge_requests/{number}/pipelines"],
        )

    async def rerun_after_description_change(
        self, *, repo: Path, branch: str, ci: CIStatus
    ) -> bool:
        """`CI_MERGE_REQUEST_DESCRIPTION` is fixed when a pipeline is created,
        so retrying jobs would re-read the old body: start a new pipeline, as
        `set_labels` does for labels."""
        raw = await git.run_git(repo, ["glab", "mr", "view", branch, "-F", "json"])
        await self._new_mr_pipeline(repo, int(mr_ops.parse_json(raw, "glab mr view")["iid"]))
        return True

    async def merge(self, *, repo: Path, branch: str, mr: MR, head: str) -> None:
        """Ask GitLab to merge, at `head` only (`--sha`): GitLab answers 409 to
        a merge request whose source branch is anywhere else, now or when its
        pipeline succeeds. On a merge train project the merge request is
        added to the train. `glab mr merge` -- which on GitLab before 19.1 can
        merge round a train -- runs only when `projects/:id` says plainly there
        is none (`_train_enabled`); a read that failed raises, as a failed
        `glab mr merge` does."""
        if not head:
            raise ForgeError("cannot merge without the head to merge")
        await git._assert_pushed(repo, branch)
        try:
            if await self._train_enabled(repo):
                await self._add_to_train(repo, branch, mr, head)
                return
            # number 0 is "not known": `run_task` does not thread the MR between
            # nodes, so the branch names it. Passing a literal 0 would target a
            # merge request that does not exist.
            target = mr_ops.target(mr.number, branch)
            await git.run_git(repo, ["glab", "mr", "merge", *target, "--yes", "--sha", head])
        except ForgeError as exc:
            if _HEAD_MOVED in str(exc).lower():
                raise HeadMoved(str(exc), await self._forge_head(repo, branch)) from exc
            raise

    async def _forge_head(self, repo: Path, branch: str) -> str:
        """The merge request's head as GitLab holds it, "" when it cannot be read."""
        try:
            found = await self.find_mr(repo=repo, branch=branch)
        except ForgeError:
            return ""
        return found.head_sha if found else ""

    async def cancel_merge(self, *, repo: Path, branch: str, mr: MRRef) -> None:
        """Take the merge request off the train, or cancel its merge when the
        pipeline succeeds. A merge request that was neither (`merge_queued`
        false) has nothing to cancel, and GitLab's refusal of that is not one."""
        try:
            if await self._train_enabled(repo):
                await git.run_git(
                    repo,
                    [
                        "glab",
                        "api",
                        "-X",
                        "DELETE",
                        f"projects/:id/merge_trains/merge_requests/{mr.number}",
                    ],
                )
            else:
                await git.run_git(
                    repo,
                    [
                        "glab",
                        "api",
                        "-X",
                        "POST",
                        f"projects/:id/merge_requests/{mr.number}/cancel_merge_when_pipeline_succeeds",
                    ],
                )
        except ForgeError as exc:
            gone = _http_status(exc) == 404 or "406" in str(exc)
            if mr.merge_queued or not gone:
                raise

    async def _add_to_train(self, repo: Path, branch: str, mr: MR, head: str) -> None:
        """Queue the merge request on the train explicitly. Before GitLab 19.1
        the merge endpoint could merge directly and bypass the train; this call
        cannot skip it, and `sha` makes GitLab refuse a head nobody reviewed."""
        iid = mr.number
        if iid <= 0:
            _, view = await self._merge_view(repo, branch)
            iid = int(view.get("iid") or 0)
        if iid <= 0:
            raise ForgeError(f"cannot queue {branch} on the merge train: no merge request")
        await git.run_git(
            repo,
            [
                "glab",
                "api",
                "-X",
                "POST",
                f"projects/:id/merge_trains/merge_requests/{iid}",
                "-f",
                "auto_merge=true",
                "-f",
                f"sha={head}",
            ],
        )

    async def _train_enabled(self, repo: Path) -> bool:
        """Whether the project merges through a merge train: both
        `merge_trains_enabled` and `merge_pipelines_enabled` are true. A field
        that is missing (GitLab Free/CE) or false, or a call GitLab refused
        (403) or does not have (404), is no train: a train endpoint that is not
        there is never a reason to stop an item. Anything else -- a 5xx, a dead
        network, a timeout, a body that is not the project object -- says
        nothing about a train and raises: read as none, `merge` would fall back
        to `glab mr merge` and could merge round the train."""
        try:
            project = await self._json(repo, ["api", "projects/:id"], "glab api project")
        except ForgeError as exc:
            if _http_status(exc) in (403, 404):
                return False
            raise
        if not isinstance(project, dict):
            raise ForgeError(f"glab api project answered without the project: {project!r:.200}")
        return (
            project.get("merge_trains_enabled") is True
            and project.get("merge_pipelines_enabled") is True
        )

    async def merge_queue(self, *, repo: Path, branch: str, base: str) -> QueueRead:
        """Where this branch's merge request stands on the project's merge
        train. `base` is unused: GitLab trains are per project, not per branch."""
        if not await self._train_enabled(repo):
            return QueueRead()
        detail, view = await self._merge_view(repo, branch)
        if detail == "merged":
            return QueueRead(kind="train", state="merged", url=str(view.get("web_url") or ""))
        iid = view.get("iid")
        if not iid:
            return QueueRead(kind="train")
        try:
            car = await self._json(
                repo, ["api", f"projects/:id/merge_trains/merge_requests/{iid}"], "glab api car"
            )
        except ForgeError as exc:
            status = _http_status(exc)
            if status == 403:
                return QueueRead()
            if status != 404:
                raise
            # GitLab destroys a car when it drops the merge request -- and when
            # it merges it: `locked` is a merge in progress, so a wait.
            if str(view.get("state")) == "locked":
                return QueueRead(kind="train", state="merging", url=str(view.get("web_url") or ""))
            if str(view.get("state")) != "opened":
                return QueueRead(kind="train", url=str(view.get("web_url") or ""))
            return await self._dropped(repo, view, str(iid))
        if not isinstance(car, dict):
            return QueueRead(kind="train", url=str(view.get("web_url") or ""))
        state = _TRAIN_CAR_STATES.get(str(car.get("status")), "queued")
        pipeline = car.get("pipeline")
        url = str(
            (pipeline.get("web_url") if isinstance(pipeline, dict) else "")
            or view.get("web_url")
            or ""
        )
        return QueueRead(kind="train", state=state, url=url)

    async def _dropped(self, repo: Path, view: dict, iid: str) -> QueueRead:
        """A merge request GitLab took off the train: why, in GitLab's words,
        and the train pipeline that failed when there is one. The train
        pipeline stays the merge request's `head_pipeline`."""
        failure = None
        head_pipeline = view.get("head_pipeline")
        if (
            isinstance(head_pipeline, dict)
            and head_pipeline.get("ref") == _TRAIN_REF.format(iid=iid)
            and head_pipeline.get("sha")
        ):
            failure = await self._read_pipeline(repo, head_pipeline, str(head_pipeline["sha"]))
        note = await self._removal_note(repo, iid)
        cause: DropCause = "other"
        # No note is no reason yet: GitLab writes it a moment after the car
        # goes, and `run._merge_dropped` reads again next poll rather than
        # reading it as a person's removal.
        reason = ""
        if note.startswith(_ABORTED_NOTE):
            cause, reason = "not_added", note[len(_ABORTED_NOTE) :]
        elif note.startswith(_REMOVED_NOTE + " because "):
            reason = note[len(_REMOVED_NOTE + " because ") :]
            # GitLab's reason may only say "not mergeable"; the merge request
            # itself says whether that is a conflict.
            if (
                "conflict" in reason.lower()
                or view.get("has_conflicts") is True
                or view.get("detailed_merge_status") == "conflict"
            ):
                cause = "conflict"
            elif failure is not None and failure.state == "failed":
                cause = "code"
        elif note.rstrip(".") in (_REMOVED_NOTE, _CANCELLED_ADD_NOTE):
            reason = note.rstrip(".")
        url = (failure.url if failure is not None else "") or str(view.get("web_url") or "")
        return QueueRead(
            kind="train",
            state="dropped",
            cause=cause,
            reason=reason,
            failure=failure,
            url=url,
        )

    async def _removal_note(self, repo: Path, iid: str) -> str:
        """The newest system note that says the merge request left the train,
        else "". A note that cannot be read is no note: the drop itself was
        already established. Newest first, a note that it joined the train
        reached before any leaving note is this entry's: GitLab has not
        written why it left yet, and an older leaving note is an earlier
        drop's, so no note either."""
        try:
            notes = await self._json(
                repo,
                [
                    "api",
                    f"projects/:id/merge_requests/{iid}/notes"
                    "?sort=desc&order_by=created_at&per_page=20",
                ],
                "glab api notes",
            )
        except ForgeError:
            return ""
        for note in notes if isinstance(notes, list) else []:
            if not isinstance(note, dict) or not note.get("system"):
                continue
            body = str(note.get("body") or "").strip()
            if body.startswith((_ABORTED_NOTE, _REMOVED_NOTE, _CANCELLED_ADD_NOTE)):
                return body
            if body.startswith(_ENQUEUE_NOTES):
                return ""
        return ""

    async def close_mr(self, *, repo: Path, mr: MRRef) -> None:
        await git.run_git(repo, ["glab", "mr", "close", str(mr.number)])

    async def reopen_mr(self, *, repo: Path, mr: MRRef) -> None:
        await git.run_git(repo, ["glab", "mr", "reopen", str(mr.number)])

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
                    merged_sha=_landed(r) if r.get("state") == "merged" else "",
                    # A merged one's `sha` can be the head GitLab rebased for a
                    # fast-forward or rebase merge, which is not a head to compare.
                    head_sha=str(r.get("sha") or "") if r.get("state") == "opened" else "",
                )
                for r in rows
            ]
        )


#: What GitLab answers (409) to a merge request merged with a `sha` that is not its
#: source branch's head.
_HEAD_MOVED = "sha does not match head of source branch"

#: `HTTP 404`, or a status that GitLab's own message follows with its name
#: later on the line ("404 Merge Train Merge Request Not Found").
_HTTP_STATUS = re.compile(
    r"\bHTTP (40[34])\b|\b(40[34])\b[^\n]*?\b(?:Not Found|Forbidden)\b", re.IGNORECASE
)


def _http_status(exc: ForgeError) -> int | None:
    """The 403 or 404 a failed `glab api` call reports, else None.

    Only what the CLI said, after `run_git`'s `<argv> failed: `: the argv holds
    the merge request's iid, which can contain "404" itself, and a timeout has
    no detail at all. A 500, a network error or a timeout is not a status here.
    """
    _, sep, detail = str(exc).partition(" failed: ")
    if not sep:
        return None
    found = _HTTP_STATUS.search(detail)
    return int(next(g for g in found.groups() if g)[:3]) if found else None


def _pipeline_number(top: dict) -> int:
    """A pipeline's id as a number, for "newest"; 0 when it is not one."""
    try:
        return int(top.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _landed(r: dict) -> str:
    """What a merged merge request landed on its target: its merge commit,
    else a squash fast-forwarded onto it, else its fast-forwarded head."""
    return str(r.get("merge_commit_sha") or r.get("squash_commit_sha") or r.get("sha") or "")
