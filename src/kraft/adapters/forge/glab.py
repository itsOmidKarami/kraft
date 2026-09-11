"""GitLab through `glab`."""

from __future__ import annotations

from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import MR, CIState, CIStatus, ForgeError, MRRef

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


class GlabCli:
    """GitLab through `glab`. Credentials stay in glab's own keyring."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        await git.assert_clean(repo)
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
                "--title",
                mr_ops.mr_title(title),
                "--description",
                body,
                "--yes",
            ],
        )
        # Read the MR back rather than parsing create's human-formatted output.
        raw = await git.run_git(repo, ["glab", "mr", "view", "-F", "json"])
        data = mr_ops.parse_json(raw, "glab mr view")
        return MR(number=int(data["iid"]), url=str(data["web_url"]))

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

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # The merge request's own state first: a conflict fails the check node
        # whatever colour the pipeline is, and `ci.poll_ci` must not wait out a
        # pipeline to learn it (Kraft-ejj9).
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
        # --ref, or this returns the newest pipeline in the whole project: a
        # green run on main would pass the gate for a red branch.
        ref = ["--ref", branch] if branch else []
        raw = await git.run_git(repo, ["glab", "ci", "list", "-F", "json", "-P", "1", *ref])
        rows = mr_ops.parse_json(raw, "glab ci list")
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
            head = await git._head_sha(repo) if sha else ""
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
            raw = await git.run_git(repo, ["glab", "ci", "get", "--pipeline-id", pid, "-F", "json"])
            data = mr_ops.parse_json(raw, "glab ci get")
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
                trace = await git.run_git(repo, ["glab", "ci", "trace", name, "--pipeline-id", pid])
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
        await git.run_git(repo, ["glab", "mr", "update", *target, "--label", ",".join(labels)])
        # `run_task` passes number 0 -- "resolve it from the checked-out branch"
        # -- to every forge handler, so that is the number this gets on the path
        # production actually takes. Skipping the re-create for it would leave
        # the one real caller with a labelled merge request and the same red
        # pipeline, which is exactly what the re-create exists to prevent.
        number = mr.number
        if number <= 0:
            raw = await git.run_git(repo, ["glab", "mr", "view", "-F", "json"])
            number = int(mr_ops.parse_json(raw, "glab mr view")["iid"])
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
                )
                for r in rows
            ]
        )
