"""GitLab through `glab`."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
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
from kraft.adapters.forge.transport import CliTransport, Transport
from kraft.automated_review import Reviewers

#: The system notes GitLab writes when someone approves or withdraws approval.
_APPROVAL_NOTES = ("approved this merge request", "unapproved this merge request")


#: GitLab's pipeline `status` vocabulary (as `glab ci list --help` listed it, 1.116.0).
#: 'skipped' is deliberately not success: nothing proved the branch green, and
#: the next node is merge. Anything unrecognised falls through to 'failed' for
#: the same reason — guessing in the direction of merging is the one guess that
#: cannot be walked back. 'canceled' is a wait, not red: a cancelled pipeline
#: is never a verdict (Kraft-zn8me). `set_labels` re-creates the MR pipeline
#: and auto-cancel of redundant pipelines cancels the old one; a pipeline a
#: person cancelled with no successor is `ci.render_ci`'s "abandoned".
def _bot_feedback(reviewer, notes: list[dict], discussions: list[dict], since) -> ReviewerFeedback:
    """One bot's part of the merge request's notes and discussions."""
    bot = reviewer.bot

    def mine(note: dict) -> bool:
        return mr_ops.same_login(str((note.get("author") or {}).get("username", "")), bot)

    summaries: list[dict] = []
    seen: list[datetime | None] = []
    for n in notes:
        # An approval, or its withdrawal, is activity, dated by its system
        # note: GitLab's approvals list says who approved, not when.
        if n.get("system") and mine(n) and n.get("body") in _APPROVAL_NOTES:
            seen.append(mr_ops.forge_time(n.get("updated_at")))
            continue
        # A system note ("added 1 commit") and an inline one (a discussion's,
        # read below) are not the bot's summary.
        if n.get("system") or n.get("position") or n.get("type") == "DiffNote" or not mine(n):
            continue
        at = mr_ops.forge_time(n.get("updated_at"))
        seen.append(at)
        if at is not None and at > since:
            summaries.append(
                {
                    "kind": "comment",
                    "state": None,
                    "body": n.get("body") or "",
                    "updated_at": n["updated_at"],
                }
            )
    threads: list[dict] = []
    for d in discussions:
        thread = d.get("notes") or []
        seen += [mr_ops.forge_time(n.get("updated_at")) for n in thread if mine(n)]
        resolvable = [n for n in thread if n.get("resolvable")]
        if (
            thread
            and mine(thread[0])
            and resolvable
            and not all(n.get("resolved") for n in resolvable)
        ):
            first = thread[0]
            where = first.get("position") or {}
            threads.append(
                {
                    "id": d["id"],
                    "file": where.get("new_path") or where.get("old_path"),
                    "line": where.get("new_line") or where.get("old_line"),
                    "body": "\n".join(
                        [
                            first.get("body") or "",
                            *(
                                f"{(n.get('author') or {}).get('username')}: {n.get('body')}"
                                for n in thread[1:]
                            ),
                        ]
                    ),
                }
            )
    return ReviewerFeedback(reviewer, mr_ops.newest(seen), tuple(summaries), tuple(threads))


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


#: glab retries a merge answered 406 (not yet mergeable) 3 times, backing off
#: from about 6s (mr_merge.go:247-271); Kraft keeps a fixed 6s. Nothing else is retried.
_MERGE_ATTEMPTS = 3
_MERGE_RETRY_DELAY = 6.0

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
#: Kraft-h81i's exposure) -- `ForgeError` from the pipeline read, a non-dict JSON
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

    def __init__(self, transport: Transport | None = None):
        self._injected = transport
        #: `(major, minor)` of each repo's GitLab, once read (`_version`).
        self._versions: dict[str, tuple[int, int]] = {}

    def _transport(self, repo: Path) -> Transport:
        return self._injected or CliTransport("glab", repo)

    async def _api(
        self,
        repo: Path,
        path: str,
        *,
        method: str = "GET",
        fields: Mapping[str, str | int | bool] | None = None,
    ):
        """The parsed JSON of one REST call; an empty body is None."""
        resp = await self._transport(repo).rest(method, path, fields=fields)
        return resp.json() if resp.text.strip() else None

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
        # The forge refuses to create against an unpushed branch.
        await self.push(repo=repo, branch=branch)
        meta = meta or mr_ops.MRMeta()
        fields: dict[str, str] = {
            "source_branch": branch,
            "target_branch": base,
            # Every MR Kraft opens starts as a draft: `human_review` is the gate
            # that decides it's ready, not this call (draft-MR workflow spec).
            # `mark_ready` un-drafts it after the gate. GitLab has no draft
            # field: the title prefix is the draft (glab mr create, :668-669).
            "title": _DRAFT + mr_ops.mr_title(title),
            "description": body,
        }
        if meta.labels:
            fields["labels"] = ",".join(meta.labels)  # GitLab creates unknown labels
        for key, names in (("assignee_ids", meta.assignees), ("reviewer_ids", meta.reviewers)):
            if names:
                fields[key] = ",".join([await self._user_id(repo, n) for n in names])
        resp = await self._transport(repo).rest(
            "POST", "projects/:id/merge_requests", fields=fields
        )
        data = resp.json()
        return MR(number=int(data["iid"]), url=str(data["web_url"]))

    async def _user_id(self, repo: Path, username: str) -> str:
        if username == "@me":  # glab's spelling for the authenticated user (user.go:16-19)
            return str((await self._api(repo, "user"))["id"])
        users = await self._api(repo, "users", fields={"username": username})
        if not users or len(users) != 1:  # glab errors on anything but exactly one
            raise ForgeError(f"GitLab has {len(users or [])} users named {username!r}, not 1")
        return str(users[0]["id"])

    async def _mr(self, repo: Path, branch: str = "") -> dict:
        """The merge request on `branch`, an open one first (the checkout's when none is
        named, as glab did), read whole: only the single-MR read carries
        `head_pipeline`."""
        if not branch:
            branch = (await git.run_git(repo, ["git", "symbolic-ref", "--short", "HEAD"])).strip()
        if not branch:
            raise ForgeError("no branch to find a merge request for")
        # Open first; a merged or closed one is still readable (glab applies no
        # state filter, mrutils.go:321-331), so only both lists empty is none.
        for state in ("opened", "all"):
            rows = await self._api(
                repo,
                "projects/:id/merge_requests",
                fields={"source_branch": branch, "state": state, "per_page": 1},
            )
            if rows:
                break
        else:
            raise ForgeError(f"no merge request on {branch}")
        return await self._api(repo, f"projects/:id/merge_requests/{rows[0]['iid']}")

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        """Un-draft the merge request: its title without the `Draft: ` prefix.
        A title that has none is already ready, so nothing is sent."""
        data = await self._view(repo, branch, mr.number)
        title = str(data.get("title") or "")
        ready = _DRAFT_PREFIX.sub("", title)
        if ready != title:
            await self._api(
                repo,
                f"projects/:id/merge_requests/{data['iid']}",
                method="PUT",
                fields={"title": ready},
            )

    async def _view(self, repo: Path, branch: str, number: int) -> dict:
        """The merge request by `number` when it is known, else by `branch`."""
        if number > 0:
            return await self._api(repo, f"projects/:id/merge_requests/{number}")
        return await self._mr(repo, branch)

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        iid = (await self._mr(repo, branch))["iid"]
        await self._api(
            repo,
            f"projects/:id/merge_requests/{iid}",
            method="PUT",
            fields={"description": body},
        )

    async def mr_body(self, *, repo: Path, branch: str) -> str:
        return str((await self._mr(repo, branch)).get("description") or "")

    async def _merge_state(self, repo: Path, branch: str = "") -> str:
        """The merge request's own view of whether it can merge (`_merge_view`)."""
        return (await self._merge_view(repo, branch))[0]

    async def _merge_view(self, repo: Path, branch: str = "") -> tuple[str, dict]:
        """The merge request's own view of whether it can merge, and the
        merge request object it came from (`{}` when there is none to give:
        a payload that is not an object, or the `find_mr` fallback below).
        `ci_status` reads the object's `head_pipeline` and `iid` too, so the
        merge request is asked once per read.

        `_mr` reads the merge request.
        `detailed_merge_status` says *why*; `merge_status` is the older,
        coarser field, kept as a fallback for an older GitLab. Anything else
        — including a payload that is not an object — is "the forge did not
        say", which `mr.mergeable` reads as undecided.

        A merge request already merged out-of-band -- a person merges it in
        the GitLab UI while this node is still polling -- reads `"state":
        "merged"` here, checked ahead of either merge-status field: a merged
        MR may not carry either (Kraft-v6ci). And if `_mr` itself
        raises -- the reported failure shape, the branch resolving to nothing
        when GitLab is asked for its current view -- fall back to `find_mr`,
        the same all-states lookup `merge`'s own "already merged" shortcut
        already trusts (Kraft-xron), before giving up: this only reads as
        "merged" when `find_mr` confirms it, so a genuine outage or auth
        failure still raises rather than being read as "someone merged it".
        """
        try:
            data = await self._mr(repo, branch)
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
        # `no_checks=False`: before the merge a missing pipeline stays a plain
        # wait; only `branch_ci_status` reports it (Kraft-rbjo3).
        return replace(
            pipeline,
            mergeable=mergeable,
            merge_detail=detail,
            block_reason=block_reason,
            no_checks=False,
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
            # the newest-pipeline listing below, which can answer with the previous
            # commit's pipeline for a few seconds after a push.
            try:
                top = await self._api(repo, f"projects/:id/pipelines/{pipeline_id}")
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
        # Newest first, one row (explicit order_by/sort below).
        rows = await self._api(
            repo,
            "projects/:id/pipelines",
            fields={
                **({"ref": branch} if branch else {}),
                "per_page": 1,
                "order_by": "id",
                "sort": "desc",
            },
        )
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
            no_checks=True,
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
            commit = await self._api(repo, f"projects/{project}/repository/commits/{sha}")
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
        genuine `script_failure` whose pipeline read happens to fail (a
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
            data = await self._api(repo, f"projects/:id/pipelines/{pipeline_id}")
            jobs_all: list = []
            page = 1
            if isinstance(data, dict):
                while True:
                    # Until a short page, as glab follows X-Next-Page (get.go:117-124).
                    got = await self._api(
                        repo,
                        f"projects/:id/pipelines/{pipeline_id}/jobs",
                        fields={"per_page": 100, "page": page},
                    )
                    # A page >= 2 that errors (here or in the except below) drops the
                    # failures already read from page 1: all-or-unreadable.
                    if not isinstance(got, list):
                        return (), _UNREADABLE_JOBS
                    jobs_all += got
                    if len(got) < 100:
                        break
                    page += 1
        except ForgeError:
            return (), _UNREADABLE_JOBS
        if not isinstance(data, dict):
            return (), _UNREADABLE_JOBS
        jobs_raw = [j for j in jobs_all if isinstance(j, dict)]
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
                resp = await self._transport(repo).rest(
                    "GET", f"projects/:id/jobs/{j.get('id')}/trace"
                )
            except ForgeError:
                continue
            detail += tuple(f"  {line}" for line in resp.text.strip().splitlines()[-15:])
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

    async def _bot_review(self, repo: Path, branch: str, bot: str) -> ReviewResult:
        """GitLab has no review state: `bot`'s unresolved discussions are
        actionable, one finding each; its approval is clean; neither yet is
        pending."""
        # ponytail: an approval is not tied to a head here -- a project that
        # keeps approvals across pushes lets an old approval settle a new head.
        mr = await self._mr(repo, branch)
        iid = mr["iid"]
        discussions = await self._api(
            repo, f"projects/:id/merge_requests/{iid}/discussions?per_page=100"
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
        approvals = await self._api(repo, f"projects/:id/merge_requests/{iid}/approvals")
        approved = any(
            mr_ops.same_login(str((a.get("user") or {}).get("username", "")), bot)
            for a in approvals.get("approved_by") or []
        )
        if approved:
            return ReviewResult("clean", detail=f"{bot}: approved")
        return ReviewResult("pending", detail=f"waiting for {bot} to review")

    async def _latest_status(self, repo: Path, mr: dict, check: str) -> dict | None:
        """The commit status (a CI job or an external status) named `check` on
        the MR's head, its latest if it ran more than once. A merged results
        pipeline's jobs report on its merge commit, not the head, so that commit
        is read too when the merge request's head pipeline is one built from
        this head (`_answered_for`)."""
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
            got = await self._api(
                repo, f"projects/{project}/repository/commits/{sha}/statuses?per_page=100"
            )
            statuses += got if isinstance(got, list) else []
        mine = sorted((s for s in statuses if s.get("name") == check), key=lambda s: s["id"])
        return mine[-1] if mine else None

    async def _check_review(self, repo: Path, branch: str, check: str) -> ReviewResult:
        """The latest commit status named `check` on the MR's head: pending
        until it finishes, clean on success, actionable on a failure."""
        mr = await self._mr(repo, branch)
        latest = await self._latest_status(repo, mr, check)
        if latest is None:
            return ReviewResult("pending", detail=f"waiting for {check} on {mr['sha'][:7]}")
        status = str(latest.get("status"))
        if status in ("success", "skipped"):
            return ReviewResult("clean", detail=f"{check}: {status}")
        if status == "failed":  # 'canceled' is never a verdict; it waits below
            return ReviewResult(
                "actionable",
                findings=(f"{check} {status}: {latest.get('description') or ''}",),
            )
        return ReviewResult("pending", detail=f"{check}: {status}")

    async def reviewer_feedback(
        self, *, repo: Path, branch: str, reviewers: Reviewers, since: datetime
    ) -> list[ReviewerFeedback]:
        """What each of `reviewers` has said on the merge request, in order. The
        merge request, and for the bots its notes and discussions, are read once
        however many reviewers there are. GitLab has no review object, so a bot's
        summaries are its top-level notes."""
        mr = await self._mr(repo, branch)
        notes = discussions = []
        if any(r.bot is not None for r in reviewers):
            # ponytail: one page of 100 of each -- paginate if an MR ever has more.
            notes = await self._api(
                repo, f"projects/:id/merge_requests/{mr['iid']}/notes?per_page=100"
            )
            discussions = await self._api(
                repo, f"projects/:id/merge_requests/{mr['iid']}/discussions?per_page=100"
            )
        out = []
        for r in reviewers:
            if r.bot is not None:
                out.append(_bot_feedback(r, notes, discussions, since))
                continue
            latest = await self._latest_status(repo, mr, r.check)
            status = None if latest is None else str(latest.get("status"))
            out.append(
                ReviewerFeedback(
                    r,
                    None,
                    check={
                        "name": r.check,
                        # 'canceled' is never a verdict: the retry comes after it.
                        "finished": status in ("success", "failed", "skipped"),
                        "conclusion": status,
                        "output": str((latest or {}).get("description") or ""),
                    },
                )
            )
        return out

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        """Retries every failed/canceled job in the pipeline `ci` read.
        A no-op on GitLab's side if none are (docs), so calling this
        speculatively ahead of a re-poll (Kraft-h81i) is safe."""
        if not ci.pipeline_ref:
            return
        await self._api(repo, f"projects/:id/pipelines/{ci.pipeline_ref}/retry", method="POST")

    async def set_labels(self, *, repo: Path, branch: str, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the merge request, then start a pipeline that can see it.

        `CI_MERGE_REQUEST_LABELS` is fixed when a pipeline is created, so a job
        that failed for a missing label re-reads the old, empty value on retry.
        Labelling alone therefore leaves the merge request looking fixed and
        still red -- the re-create is part of the capability, not the caller's
        homework (Kraft-xh0q).

        GitLab's free tier does not enforce a scoped label's exclusivity
        server-side, so `add_labels` alone only adds: a second repair pass leaves
        both `release::minor` and `release::patch` on the MR, and
        `next_tag.py` now raises on more than one `release::` label rather
        than picking one (Kraft-zfdu8). So any current label sharing a new
        label's `scope::` prefix is dropped in the same call.
        """
        if not labels:
            return
        # Also the read that resolves the number for the pipeline re-create below:
        # every caller needs the MR's current labels too, so one read serves both.
        data = await self._view(repo, branch, mr.number)
        number = mr.number if mr.number > 0 else int(data["iid"])
        current = [str(label) for label in data.get("labels") or []]
        drop = mr_ops.same_scope_labels(current, labels)
        fields = {"add_labels": ",".join(labels)}
        if drop:
            fields["remove_labels"] = ",".join(drop)
        await self._api(repo, f"projects/:id/merge_requests/{number}", method="PUT", fields=fields)
        await self._new_mr_pipeline(repo, number)

    async def _new_mr_pipeline(self, repo: Path, number: int) -> None:
        # `projects/:id` is glab's own placeholder for the repo the command is
        # run in, so this stays as repo-agnostic as every other call here.
        await self._api(repo, f"projects/:id/merge_requests/{number}/pipelines", method="POST")

    async def rerun_after_description_change(
        self, *, repo: Path, branch: str, ci: CIStatus
    ) -> bool:
        """`CI_MERGE_REQUEST_DESCRIPTION` is fixed when a pipeline is created,
        so retrying jobs would re-read the old body: start a new pipeline, as
        `set_labels` does for labels."""
        await self._new_mr_pipeline(repo, int((await self._mr(repo, branch))["iid"]))
        return True

    async def merge(self, *, repo: Path, branch: str, mr: MR, head: str) -> None:
        """Ask GitLab to merge, at `head` only (`sha`): GitLab answers 409 to
        a merge request whose source branch is anywhere else, now or when its
        pipeline succeeds. On a merge train project the merge request is
        added to the train. Otherwise `PUT .../merge`, as glab sends it
        (mr_merge.go:221-231): the auto-merge flag whenever the head pipeline
        exists (GitLab decides whether to wait for it), none when there is no
        pipeline, and no request at all for a failed or canceled one; a 406 is
        retried (:247-271). Where Kraft differs: glab 1.117 never sends
        `merge_when_pipeline_succeeds`, but a self-managed GitLab before 17.11
        has no `auto_merge` and would merge at once over a running pipeline,
        so below 17.11 that older field is sent (an unreadable version sends
        `auto_merge`, as glab). The pipeline is `head_pipeline` first where
        glab reads `pipeline`; the two are one object on GitLab. A project
        read that failed raises: read as no train, the PUT could merge round
        one (before GitLab 19.1)."""
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
            data = await self._view(repo, branch, mr.number)
            if data.get("draft"):  # mrutils.go:61-63: glab refuses a draft before any PUT
                raise ForgeError(f"cannot merge !{data['iid']}: still a draft")
            pipeline = data.get("head_pipeline") or data.get("pipeline")
            fields: dict[str, str | bool] = {}
            if isinstance(pipeline, dict):
                if pipeline.get("status") in ("failed", "canceled"):
                    raise ForgeError(
                        f"cannot merge !{data['iid']}: its pipeline {pipeline['status']}"
                    )
                old = (await self._version(repo) or (99, 0)) < (17, 11)
                fields["merge_when_pipeline_succeeds" if old else "auto_merge"] = True
            fields["sha"] = head
            path = f"projects/:id/merge_requests/{data['iid']}/merge"
            for attempt in range(_MERGE_ATTEMPTS):
                try:
                    await self._api(repo, path, method="PUT", fields=fields)
                    break
                except ForgeError as exc:
                    if exc.status != 406 or attempt == _MERGE_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(_MERGE_RETRY_DELAY)
        except ForgeError as exc:
            # A 409 is a moved head only when GitLab's head is another one: the
            # train POST also answers 409 for states that move nothing.
            if exc.status == 409:
                current = await self._forge_head(repo, branch)
                if current != head:
                    raise HeadMoved(str(exc), current) from exc
            raise

    async def _forge_head(self, repo: Path, branch: str) -> str:
        """The merge request's head as GitLab holds it, "" when it cannot be read."""
        try:
            found = await self.find_mr(repo=repo, branch=branch)
        except ForgeError:
            return ""
        return found.head_sha if found else ""

    async def cancel_merge(self, *, repo: Path, branch: str, mr: MRRef) -> None:
        """Cancel the merge request's auto-merge, which also takes it off the
        merge train (GitLab has no endpoint that removes one from a train
        alone). A merge request that was neither queued nor set to merge
        (`merge_queued` false) has nothing to cancel, and GitLab's refusal of
        that is not a failure. GitLab 19.5+ has `cancel_auto_merge` (409 when
        there is nothing to cancel); before it, `cancel_merge_when_pipeline_succeeds`
        answers `201` with `{"status": "error"}` when it cancelled nothing,
        which the transport takes for success, so the body is read."""
        new = await self._cancel_auto_merge_exists(repo)
        endpoint = "cancel_auto_merge" if new else "cancel_merge_when_pipeline_succeeds"
        try:
            body = await self._api(
                repo, f"projects/:id/merge_requests/{mr.number}/{endpoint}", method="POST"
            )
        except ForgeError as exc:
            gone = exc.status in ((404, 409) if new else (404, 406))
            if mr.merge_queued or not gone:
                raise
            return
        if isinstance(body, dict) and body.get("status") == "error" and mr.merge_queued:
            raise ForgeError(
                f"glab could not cancel the merge of !{mr.number}: {body.get('message', body)}"
            )

    async def _cancel_auto_merge_exists(self, repo: Path) -> bool:
        """GitLab 19.5+ (the old endpoint is deprecated there, not removed, so
        a version that cannot be read falls back to it)."""
        version = await self._version(repo)
        return version is not None and version >= (19, 5)

    async def _version(self, repo: Path) -> tuple[int, int] | None:
        """`(major, minor)` of the repo's GitLab, None when it cannot be read.
        Never raises; only a read that worked is kept."""
        key = str(repo)
        if key not in self._versions:
            try:
                got = str((await self._api(repo, "version"))["version"])
            except (ForgeError, TypeError, KeyError, ValueError):
                return None
            found = re.match(r"(\d+)\.(\d+)", got)
            if not found:
                return None
            self._versions[key] = (int(found[1]), int(found[2]))
        return self._versions[key]

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
        await self._api(
            repo,
            f"projects/:id/merge_trains/merge_requests/{iid}",
            method="POST",
            fields={"auto_merge": "true", "sha": head},
        )

    async def _train_enabled(self, repo: Path) -> bool:
        """Whether the project merges through a merge train: both
        `merge_trains_enabled` and `merge_pipelines_enabled` are true. A field
        that is missing (GitLab Free/CE) or false, or a call GitLab refused
        (403) or does not have (404), is no train: a train endpoint that is not
        there is never a reason to stop an item. Anything else -- a 5xx, a dead
        network, a timeout, a body that is not the project object -- says
        nothing about a train and raises: read as none, `merge` would fall back
        to the plain merge PUT and could merge round the train."""
        try:
            project = await self._api(repo, "projects/:id")
        except ForgeError as exc:
            if exc.status in (403, 404):
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
            car = await self._api(repo, f"projects/:id/merge_trains/merge_requests/{iid}")
        except ForgeError as exc:
            status = exc.status
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
            notes = await self._api(
                repo,
                f"projects/:id/merge_requests/{iid}/notes?sort=desc&order_by=created_at&per_page=20",
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
        await self._state_event(repo, mr, "close")

    async def reopen_mr(self, *, repo: Path, mr: MRRef) -> None:
        await self._state_event(repo, mr, "reopen")

    async def _state_event(self, repo: Path, mr: MRRef, event: str) -> None:
        await self._api(
            repo,
            f"projects/:id/merge_requests/{mr.number}",
            method="PUT",
            fields={"state_event": event},
        )

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        # state=all, or a merged merge request reads as no merge request at all.
        rows = await self._api(
            repo,
            "projects/:id/merge_requests",
            fields={"source_branch": branch, "state": "all", "per_page": 5},
        )
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
                for r in rows or []
            ]
        )

    async def mr_details(self, *, repo: Path, branch: str) -> MRDetails | None:
        ref = await self.find_mr(repo=repo, branch=branch)
        if ref is None:
            return None
        r = await self._api(repo, f"projects/:id/merge_requests/{ref.number}") or {}
        return MRDetails(
            number=ref.number,
            url=str(r.get("web_url") or ref.url),
            state=_GLAB_MR_STATES.get(str(r.get("state", "")), "closed"),
            title=str(r.get("title") or ""),
            body=str(r.get("description") or ""),
            # `work_in_progress` is what servers before `draft` (GitLab 13.x) say.
            draft=bool(r.get("draft", r.get("work_in_progress"))),
            labels=tuple(str(x) for x in r.get("labels") or ()),
            source_branch=str(r.get("source_branch") or ""),
            target_branch=str(r.get("target_branch") or ""),
            head_sha=str(r.get("sha") or ""),
        )

    async def pipelines(self, *, repo: Path, branch: str, base: str) -> tuple[Pipeline, ...]:
        mr = await self.find_mr(repo=repo, branch=branch)
        if mr:
            path, fields = f"projects/:id/merge_requests/{mr.number}/pipelines", {"per_page": 20}
        else:
            path, fields = "projects/:id/pipelines", {"ref": branch, "per_page": 20}
        kinds = (
            {
                _TRAIN_REF.format(iid=mr.number): "train",
                f"refs/merge-requests/{mr.number}/head": "merge_request",
                f"refs/merge-requests/{mr.number}/merge": "merge_request",
            }
            if mr
            else {}
        )
        return tuple(
            Pipeline(
                id=str(p["id"]),
                kind=kinds.get(str(p.get("ref", "")), "branch"),
                ref=str(p.get("ref") or ""),
                sha=str(p.get("sha") or ""),
                status=str(p.get("status") or ""),
                url=str(p.get("web_url") or ""),
                created_at=str(p.get("created_at") or ""),
            )
            for p in await self._api(repo, path, fields=fields) or ()
        )

    async def jobs(self, *, repo: Path, pipeline_id: str) -> tuple[Job, ...]:
        rows = await self._api(
            repo,
            f"projects/:id/pipelines/{pipeline_id}/jobs",
            fields={"per_page": 100, "include_retried": False},
        )
        return tuple(
            Job(
                id=str(j["id"]),
                name=str(j.get("name") or ""),
                stage=str(j.get("stage") or ""),
                status=str(j.get("status") or ""),
                failure_reason=str(j["failure_reason"]) if j.get("failure_reason") else None,
                url=str(j.get("web_url") or ""),
            )
            for j in rows or ()
        )

    async def job_log(self, *, repo: Path, job_id: str) -> str:
        return (await self._transport(repo).rest("GET", f"projects/:id/jobs/{job_id}/trace")).text

    async def merged_mrs(self, *, repo: Path, base: str, limit: int) -> tuple[MergedMR, ...]:
        fields = {
            "state": "merged",
            "target_branch": base,
            "order_by": "merged_at",
            "sort": "desc",
            # One page: the route caps `limit` at 100, GitLab's page size.
            "per_page": min(limit, 100),
        }
        try:
            rows = await self._api(repo, "projects/:id/merge_requests", fields=fields)
        except ForgeError as exc:
            if exc.status != 400:
                raise
            # Before GitLab 17.2 `merged_at` is not an `order_by` value.
            rows = await self._api(
                repo,
                "projects/:id/merge_requests",
                fields={**fields, "order_by": "updated_at"},
            )
        return tuple(
            MergedMR(
                number=int(r["iid"]),
                title=str(r.get("title") or ""),
                labels=tuple(str(x) for x in r.get("labels") or ()),
                url=str(r.get("web_url") or ""),
                merged_at=str(r.get("merged_at") or ""),
            )
            # Newest merge first even when the old-server fallback ordered by update.
            for r in sorted(rows or (), key=lambda r: str(r.get("merged_at") or ""), reverse=True)[
                :limit
            ]
        )

    async def labels(self, *, repo: Path) -> tuple[Label, ...]:
        out: list[Label] = []
        for page in range(1, 11):
            rows = await self._api(
                repo,
                "projects/:id/labels",
                fields={"per_page": 100, "include_ancestor_groups": True, "page": page},
            )
            out += [
                Label(
                    name=str(r["name"]),
                    description=str(r.get("description") or ""),
                    color=str(r.get("color") or ""),
                )
                for r in rows or ()
            ]
            if len(rows or ()) < 100:
                break
        return tuple(out)


#: How GitLab marks a draft: the title's prefix, which `mark_ready` strips as
#: glab's `mr update --ready` does (:234-238).
_DRAFT = "Draft: "
_DRAFT_PREFIX = re.compile(r"^(\s*(draft:|wip:)\s*)+", re.IGNORECASE)


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
