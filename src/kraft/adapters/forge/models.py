"""The forge's vocabulary: the states, errors and shapes every backend and
every node speaks in.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    # `mr.py` imports `ForgeError`/`MRRef` from here -- a real-time import
    # the other way would be circular. `from __future__ import annotations`
    # already makes every annotation below lazy, so this is type-checking
    # only.
    from collections.abc import Mapping
    from datetime import datetime

    from kraft.adapters.forge.mr import MRMeta
    from kraft.automated_review import AutomatedReview, Reviewers

CIState = Literal["pending", "success", "failed"]

#: Why a forge call failed, from the HTTP status (`reason_for`) or a GraphQL
#: error's type. None on an error means no request was made or nothing is known.
Reason = Literal[
    "auth",
    "forbidden",
    "rate_limited",
    "not_found",
    "conflict",
    "head_moved",
    "unreachable",
    "tls",
]


class ForgeError(RuntimeError):
    """The forge could not be reached, or answered something unusable.

    `status` and `reason` are set when a request was answered; `retry_after`
    (seconds) and `sso_url` come from the response headers."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: Reason | None = None,
        retry_after: float | None = None,
        sso_url: str = "",
    ):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.retry_after = retry_after
        self.sso_url = sso_url


def reason_for(status: int, headers: Mapping[str, str]) -> Reason | None:
    """The `Reason` an HTTP status means; header names are lower-cased. A 403 is
    a rate limit only when the forge says so, otherwise a refused permission."""
    if status == 401:
        return "auth"
    if status == 429:
        return "rate_limited"
    if status == 403:
        limited = headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
        return "rate_limited" if limited else "forbidden"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status >= 500:
        return "unreachable"
    return None


class HeadMoved(ForgeError):
    """The forge refused a merge because the head it holds is not the one named
    (`Forge.merge(head=)`): a commit reached the merge request that nobody
    reviewed. `current` is the forge's head, "" when it could not be read."""

    def __init__(self, message: str, current: str = ""):
        super().__init__(message, reason="head_moved")
        self.current = current


class ForeignCommits(ForgeError):
    """A push was refused because the remote branch holds commits Kraft did not
    push and the worktree does not have (`git.push`). `remote` is its tip."""

    def __init__(self, message: str, remote: str = ""):
        super().__init__(message)
        self.remote = remote


#: What a forge or git failure's own text says about why, lower-cased and
#: matched as substrings: a rejected credential first (it is also what an
#: unreachable-looking retry would not fix), then a host that could not be
#: reached. The CLIs print no code for either, so the words are the only handle.
_AUTH_MARKERS = (
    "authentication failed",
    "could not read username",
    "permission denied (publickey)",
    "bad credentials",
    "http 401",
    "http 403",
    "401 unauthorized",
    "403 forbidden",
    "gh auth login",
    "glab auth login",
    "invalid token",
    # git's own HTTPS refusals print no marker above.
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "invalid username or password",
    "http basic: access denied",
)
_UNREACHABLE_MARKERS = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection timed out",
    "operation timed out",
    "connection refused",
    "network is unreachable",
    "failed to connect",
    "could not connect",
    "timed out after",
)
#: Local git operations Kraft runs itself. Their messages carry a file name or
#: git's own timeout, neither of which says anything about a forge: a file
#: called "403 forbidden.txt" is not a refused credential, and a rebase that
#: ran out of time did not fail to reach a host.
_LOCAL_GIT = ("git rebase ", "git worktree add timed out")


#: What each `ForgeError.reason` means for a stop. A reason missing here (a rate
#: limit, a missing project, a conflict, a moved head) is no auth or network
#: cause.
_CAUSE_OF_REASON: dict[str, Literal["forge_auth", "forge_unreachable"]] = {
    "auth": "forge_auth",
    "forbidden": "forge_auth",
    "unreachable": "forge_unreachable",
    "tls": "forge_unreachable",
}


def failure_cause(
    text: str, record: Mapping[str, object] | None = None
) -> Literal["forge_auth", "forge_unreachable"] | None:
    """Why a forge or git failure says it failed, when it says: a credential the
    forge refused (`forge_auth`, fixed by signing the forge CLI in on the
    server's machine, not in the repo's settings) or a forge that could not be
    reached (`forge_unreachable`). None when it names neither.

    `record` is the failed task's forge-error record (`{"reason": ...}`); one
    with a reason decides. Without one -- a row written before the record, or an
    error from a call that made no request, like a CLI that is not signed in --
    the text's markers decide."""
    if record and record.get("reason"):
        return _CAUSE_OF_REASON.get(str(record["reason"]))
    low = text.lower()
    if any(m in low for m in _LOCAL_GIT):
        return None
    if any(m in low for m in _AUTH_MARKERS):
        return "forge_auth"
    if any(m in low for m in _UNREACHABLE_MARKERS):
        return "forge_unreachable"
    return None


ApprovalState = Literal["pending", "approved"]

QueueKind = Literal["none", "train", "queue"]
QueueState = Literal["absent", "queued", "merging", "merged", "dropped"]
DropCause = Literal["code", "conflict", "not_added", "other"]  # "infra" is decided in run.py


@dataclass(frozen=True)
class ReviewResult:
    """One read of the automated review, in the only vocabulary a template
    sees (`automated-review-task-uses-ordinary-task-results`). Which bot,
    which check, which webhook produced it is the backend's business
    (`automated-review-implementation-is-not-template-configuration`)."""

    state: Literal["pending", "clean", "actionable", "error"]
    #: One entry per piece of actionable feedback; what the node's recovery
    #: and fix loop are handed as findings.
    findings: tuple[str, ...] = ()
    #: What the reviewer said, for the log a human reads.
    detail: str = ""
    #: False when the repository names no reviewer: settled clean because no
    #: review is expected, which is recorded as such rather than as a pass.
    configured: bool = True


@dataclass(frozen=True)
class ReviewerFeedback:
    """What one reviewer has said on the merge request, as the review wait
    reads it to decide whether the reviewer has finished with the latest push
    (`settle.decide`) and to hand on what it said. Nothing here says how the
    forge was asked."""

    reviewer: AutomatedReview
    #: The newest comment, review or inline-comment update by this bot, on the
    #: forge's clock; None when it has done none.
    last_activity: datetime | None
    #: The bot's top-level comments and submitted reviews since the push:
    #: `{"kind": "comment" | "review", "state": str | None, "body": str,
    #: "updated_at": iso}`.
    summaries: tuple[dict, ...] = ()
    #: Its unresolved inline threads, from any push: `{"id": str, "file": str |
    #: None, "line": int | None, "body": str}`.
    threads: tuple[dict, ...] = ()
    #: A `check:` reviewer's check: `{"name", "finished": bool, "conclusion":
    #: str | None, "output": str}`; None for a bot.
    check: dict | None = None


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
    #: The forge already holds a merge request for it -- auto-merge enabled,
    #: merge when the pipeline succeeds -- and will land it on its own. The
    #: forge's record, so it outlives any restart of Kraft's own wait
    #: (Kraft-l98h6): nothing asks it to merge a second time.
    merge_queued: bool = False
    #: The revision a merged one landed on its target, "" when the forge did
    #: not say -- what a workspace root's pointer names (Kraft-n60oh).
    merged_sha: str = ""
    #: The forge's current head of the merge request, "" when it did not say
    #: (or, on GitLab, once merged: the head it reports then may be a rebase's
    #: landed one). What a merge Kraft asked for is compared against.
    head_sha: str = ""


@dataclass(frozen=True)
class FailedJob:
    """One failed job/check, as structured triage data rather than a log
    line (Kraft-cbr §1). `failure_reason` is the forge's own vocabulary
    (GitLab: `script_failure`, `runner_system_failure`, ...; gh: a
    conclusion coarsened the same way) — None when the backend has nothing
    finer than "it failed"."""

    name: str
    status: str
    failure_reason: str | None = None
    #: gh only: the check's `detailsUrl`, so `GhCli.retry_jobs` can recover
    #: the Actions run id without a second API call. None on GitLab, and on
    #: gh whenever the forge didn't say.
    detail_url: str | None = None


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
    #: not decided, or does not say — see `mr.mergeable`. Defaulted so FakeForge
    #: and every existing caller are unaffected.
    mergeable: bool | None = None
    #: The raw state the forge gave, for the log line a human reads.
    merge_detail: str = ""
    #: Why `mergeable` is anything but `True`, when something here
    #: recognises the state -- distinct problems with distinct fixes
    #: (`mr.classify_block_reason`). `None` same as `mergeable`'s own
    #: `None`: nothing here recognised the state, or there was nothing to
    #: recognise. Defaulted so FakeForge and every existing caller are
    #: unaffected.
    block_reason: Literal["draft", "conflict", "not_approved"] | None = None
    #: The commit this pipeline/check-run answered for. "" means the backend
    #: could not tell — an old FakeForge literal, or a call this field
    #: predates — so `ci_poll`'s sha guard (Kraft-bjjm) has nothing to
    #: compare and falls back to trusting the read, exactly today's behaviour.
    sha: str = ""
    #: One entry per failed job, empty for a green or still-pending pipeline.
    failed_jobs: tuple[FailedJob, ...] = ()
    #: GitLab's numeric pipeline id, opaque outside `GlabCli.retry_jobs`. ""
    #: on gh, whose `retry_jobs` works from `failed_jobs[*].detail_url` instead.
    pipeline_ref: str = ""
    #: When the run this pending read stands on was cancelled (the forge's own
    #: timestamp), set only when a cancel is the *only* thing it is waiting
    #: on: every other check settled and none red. "" otherwise, or when the
    #: forge gave no time. `render_ci` reads it to tell a cancel whose
    #: successor is still registering from one nobody followed up
    #: (Kraft-kbqmk).
    cancelled_at: str = ""
    #: The head has no checks registered at all. Pending while they are still
    #: on their way, but on a pull request that conflicts with its base they
    #: never come (Kraft-09ze6, gh's `ci_status` only), and neither do they on
    #: a branch whose CI does not run on a push (Kraft-rbjo3, both backends'
    #: `branch_ci_status`).
    no_checks: bool = False


@dataclass(frozen=True)
class QueueRead:
    """Where a merge request stands in the repo's merge train or merge queue,
    in the forge-neutral vocabulary. The default is a repository with neither."""

    kind: QueueKind = "none"
    state: QueueState = "absent"
    #: Set only when `state == "dropped"`.
    cause: DropCause | None = None
    #: The forge's own words, verbatim.
    reason: str = ""
    #: The failed train/queue pipeline or checks.
    failure: CIStatus | None = None
    #: That pipeline/run, else the merge request.
    url: str = ""


class Forge(Protocol):
    async def open_mr(
        self,
        *,
        repo: Path,
        branch: str,
        base: str,
        title: str,
        body: str,
        meta: MRMeta | None = None,
        check_clean: bool = True,
    ) -> MR:
        """A draft merge request from `branch` into `base`, the item's base
        branch in `repo` (`builtins.base_branch`). `check_clean` refuses a
        dirty `repo` first; an in-place item's checkout is the person's, so
        it passes False."""
        ...

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None: ...
    async def push(self, *, repo: Path, branch: str) -> None: ...
    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None: ...
    async def mr_body(self, *, repo: Path, branch: str) -> str: ...
    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str, pipeline_id: str = ""
    ) -> CIStatus: ...
    async def branch_ci_status(
        self, *, repo: Path, branch: str, head_sha: str, pipeline_id: str = ""
    ) -> CIStatus: ...
    async def merge(self, *, repo: Path, branch: str, mr: MR, head: str) -> None:
        """Ask the forge to merge `mr`, and only at `head`, the full sha it may
        merge: the forge refuses (`HeadMoved`) any other, now or whenever it
        gets round to merging. An empty `head` raises `ForgeError` unasked."""
        ...

    async def cancel_merge(self, *, repo: Path, branch: str, mr: MRRef) -> None:
        """Undo what `merge` asked: auto-merge, merge when the pipeline
        succeeds, a queue or train entry. Raises `ForgeError` when it cannot,
        because the forge may still merge."""
        ...

    async def close_mr(self, *, repo: Path, mr: MRRef) -> None: ...
    async def reopen_mr(self, *, repo: Path, mr: MRRef) -> None: ...
    async def set_labels(
        self, *, repo: Path, branch: str, mr: MR, labels: tuple[str, ...]
    ) -> None: ...
    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None: ...
    async def merge_queue(self, *, repo: Path, branch: str, base: str) -> QueueRead: ...
    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None: ...
    async def rerun_after_description_change(
        self, *, repo: Path, branch: str, ci: CIStatus
    ) -> bool:
        """Start the failed checks again after the description was edited, so
        they read the new one. True when that made a NEW pipeline, which the
        caller's stored pipeline ref must stop pinning past."""
        ...

    async def approval_state(self, *, repo: Path, branch: str) -> ApprovalState: ...
    async def automated_review(
        self, *, repo: Path, branch: str, reviewers: Reviewers
    ) -> ReviewResult: ...
    async def reviewer_feedback(
        self, *, repo: Path, branch: str, reviewers: Reviewers, since: datetime
    ) -> list[ReviewerFeedback]: ...


def _head(repo: Path) -> str | None:
    """`repo`'s checked-out head, for `FakeForge`; None when `repo` is not a
    git checkout (most fake-forge tests hand it a bare directory)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


@dataclass
class FakeForge:
    """In-memory forge for tests.

    `ci_states` is consumed one call at a time so a test can script
    pending-then-green without sleeping or polling; the last state repeats
    forever, so a test that only cares about the end state passes one. Every
    other wait's script -- `review_results`, `approval_states`,
    `branch_ci_states` -- works the same way, and `merge_delay` holds a
    requested merge open for that many reads: so every external wait can be
    walked pending-then-settled.
    """

    ci_states: list[CIState] = field(default_factory=lambda: ["success"])
    #: Parallel to `ci_states`: the `sha` each successive `ci_status` call
    #: reports. Consumed the same one-at-a-time way; repeats its last entry.
    #: Defaults to "" — "the backend didn't say" — so a test that never sets
    #: this is unaffected by the sha guard (Kraft-bjjm).
    ci_shas: list[str] = field(default_factory=lambda: [""])
    #: Parallel to `ci_states`, for a settled red pipeline. Defaults to no
    #: failed jobs, which `ci.is_infra_red` reads as infra-shaped (a red
    #: pipeline with nothing to blame is exactly the zero-job case) — a test
    #: that wants a code-red must set this explicitly.
    ci_failed_jobs: list[tuple[FailedJob, ...]] = field(default_factory=lambda: [()])
    #: Times `retry_jobs` was called, for a test to assert the self-retry
    #: fired (Kraft-h81i) without a real forge to observe.
    retried: list[str] = field(default_factory=list)
    #: Every `pipeline_id` a caller passed to `ci_status`, in call order
    #: ("" for an unpinned call), so a test can see it threaded through
    #: without a real GitLab (Kraft-ivh1).
    pipeline_ids_requested: list[str] = field(default_factory=list)
    #: Parallel to `ci_states`: the pipeline id each successive `ci_status`
    #: call reports back (Kraft-ivh1). Defaults to "" -- a test that never
    #: sets this exercises no pinning, same as `ci_shas`' default. Task 4
    #: needs this to make on.ci.poll's persisted `ci_pipeline_ref` non-empty
    #: against a fake forge.
    ci_pipeline_refs: list[str] = field(default_factory=lambda: [""])
    #: Parallel to `ci_states`: each `ci_status` call's `cancelled_at`.
    ci_cancelled_at: list[str] = field(default_factory=lambda: [""])
    #: Every `ci_status` call's `no_checks`.
    ci_no_checks: bool = False
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
    #: What `ci_status` says is blocking the merge request, alongside
    #: `mergeable`/`merge_detail`. `None` (nothing blocking) is the
    #: default, so tests that predate this are unaffected.
    block_reason: Literal["draft", "conflict", "not_approved"] | None = None
    #: Labels `set_labels` put on the merge request, in call order.
    labels: list[str] = field(default_factory=list)
    #: repo each `opened` number belongs to (Kraft-qlsf). The real CLIs
    #: disambiguate two repos sharing a branch name by their own cwd; a fake
    #: keyed on branch alone would let a submodule's `find_mr` "discover" the
    #: root's merge request just because both are on `kraft/<item>`.
    _opened_repo: dict[int, str] = field(default_factory=dict)
    #: Title each `open_mr` call was given, keyed by the number returned --
    #: so a test can assert the authored title reached the forge without a
    #: network.
    opened_titles: dict[int, str] = field(default_factory=dict)
    #: Draft state per MR number, keyed the same way as `opened_titles`.
    #: `open_mr` always sets this True now (draft-MR workflow spec);
    #: `mark_ready` (Task 2) is the only thing that flips it.
    opened_draft: dict[int, bool] = field(default_factory=dict)
    #: `meta` each `open_mr` call was given, same keying as `opened_titles` --
    #: so a test can assert labels/assignees/reviewers reached the forge.
    opened_meta: dict[int, MRMeta] = field(default_factory=dict)
    #: Body each `open_mr` call was given, alongside `bodies` (which only
    #: `update_mr`/`sync_mr` write to).
    opened_bodies: dict[int, str] = field(default_factory=dict)
    #: Whether each `open_mr` was asked to refuse a dirty checkout.
    opened_check_clean: dict[int, bool] = field(default_factory=dict)
    #: The branch each opened merge request targets, by number.
    opened_base: dict[int, str] = field(default_factory=dict)
    #: `automated_review`'s answers, consumed like `ci_states`. A bare state
    #: string is shorthand for a `ReviewResult` with nothing else to say.
    review_results: list[ReviewResult | str] = field(default_factory=lambda: ["clean"])
    #: The reviewers each `automated_review` read was asked for, in order.
    reviewers_asked: list[Reviewers] = field(default_factory=list)
    #: `reviewer_feedback`'s answers, one list per call, consumed like
    #: `review_results`. Empty, each reviewer answers with nothing to say: a
    #: bot with no activity, a check that finished and passed.
    feedback_script: list[list[ReviewerFeedback]] = field(default_factory=list)
    #: `approval_state`'s answers, consumed like `ci_states`.
    approval_states: list[ApprovalState] = field(default_factory=lambda: ["approved"])
    #: `branch_ci_status`'s own script, for the post-merge pipeline; `None`
    #: shares `ci_states` with the merge request's pipeline.
    branch_ci_states: list[CIState] | None = None
    #: How many `find_mr` reads of a merge request after its `merge` still
    #: find it open, its merge queued -- a forge that merges when its checks
    #: pass rather than at once.
    merge_delay: int = 0
    #: Merges queued but not landed yet, each merge request's own: number ->
    #: reads of it left. Reading one never moves another.
    _landing: dict[int, int] = field(default_factory=dict)
    #: The head each queued merge was asked for at. A push of a different
    #: head drops the queued merge, as a forge drops it for commits nobody
    #: asked it to merge.
    _queued_head: dict[int, str | None] = field(default_factory=dict)
    #: The head each merge request merged at, by number.
    _merged_head: dict[int, str | None] = field(default_factory=dict)
    #: Numbers `close_mr` has closed (B4). `find_mr` reports these as
    #: `closed` unless they also merged -- a merge is forever, a close is
    #: reversible by `reopen_mr`.
    closed: list[int] = field(default_factory=list)
    #: Numbers `reopen_mr` has put back, in call order -- a test's receipt
    #: that the call landed, the same role `retried`/`merged` play.
    reopened: list[int] = field(default_factory=list)
    #: Every merge request number `merge` was called with, queued, dropped or
    #: landed alike -- a test's receipt of how often the forge was asked.
    merge_calls: list[int] = field(default_factory=list)
    #: The repo's merge train or queue, as `merge_queue` reports its kind.
    queue_kind: QueueKind = "none"
    #: What each `merge` call on a fake with a train or queue does, consumed one
    #: per call: the merge request is dropped, and stays so until the next
    #: `merge`. With none left, `merge_delay` behaves as before.
    queue_drops: list[QueueRead] = field(default_factory=list)
    #: Drops consumed and not yet superseded by another `merge`, by number.
    _dropped: dict[int, QueueRead] = field(default_factory=dict)
    #: The head the forge holds for the merge request, when it is not the one
    #: Kraft pushed: `merge` refuses any other `head` as the real forges do
    #: (`HeadMoved`), and `find_mr` reports it as `head_sha`.
    forge_head: str | None = None
    #: A head the forge comes to hold only once it has accepted a `merge`: a
    #: commit that reaches the branch after Kraft asked.
    head_after_merge: str | None = None
    #: The `head` each `merge` call was handed, refused or not.
    merge_heads: list[str] = field(default_factory=list)
    #: Numbers `cancel_merge` has cancelled; a cancelled merge never lands.
    cancelled: list[int] = field(default_factory=list)
    #: When set, `push` refuses as `git.push` does for a commit the forge has
    #: that the worktree lacks (`ForeignCommits`), naming this as the remote.
    push_refused: str | None = None
    #: The head each merge request was last asked to merge at, by number.
    _asked_head: dict[int, str] = field(default_factory=dict)

    @staticmethod
    def _next(script: list):
        """The script's next answer; its last repeats forever."""
        return script.pop(0) if len(script) > 1 else script[0]

    async def open_mr(
        self,
        *,
        repo: Path,
        branch: str,
        base: str,
        title: str,
        body: str,
        meta: MRMeta | None = None,
        check_clean: bool = True,
    ) -> MR:
        from kraft.adapters.forge.mr import MRMeta

        number = len(self.opened) + 1
        self.opened[number] = branch
        self.opened_base[number] = base
        self._opened_repo[number] = str(repo)
        self.opened_titles[number] = title
        self.opened_draft[number] = True
        self.opened_meta[number] = meta or MRMeta()
        self.opened_bodies[number] = body
        self.opened_check_clean[number] = check_clean
        return MR(number=number, url=f"http://fake.forge/{number}")

    async def mark_ready(self, *, repo: Path, branch: str, mr: MR) -> None:
        self.opened_draft[mr.number] = False

    async def push(self, *, repo: Path, branch: str) -> None:
        if self.push_refused:
            raise ForeignCommits(
                f"origin/{branch} is at {self.push_refused[:12]}, which Kraft did not push "
                "and this worktree does not have",
                remote=self.push_refused,
            )
        self.pushed.append(branch)
        head = _head(repo)
        for number in list(self._landing):
            asked_at = self._queued_head[number]
            same_mr = self.opened[number] == branch and self._matches(number, repo)
            if same_mr and asked_at is not None and head is not None and head != asked_at:
                del self._landing[number], self._queued_head[number]

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        self.bodies[branch] = body

    async def mr_body(self, *, repo: Path, branch: str) -> str:
        if branch in self.bodies:
            return self.bodies[branch]
        return next((b for n, b in self.opened_bodies.items() if self.opened[n] == branch), "")

    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        self.pipeline_ids_requested.append(pipeline_id)
        state = self.ci_states.pop(0) if len(self.ci_states) > 1 else self.ci_states[0]
        sha = self.ci_shas.pop(0) if len(self.ci_shas) > 1 else self.ci_shas[0]
        failed_jobs = (
            self.ci_failed_jobs.pop(0) if len(self.ci_failed_jobs) > 1 else self.ci_failed_jobs[0]
        )
        pipeline_ref = (
            self.ci_pipeline_refs.pop(0)
            if len(self.ci_pipeline_refs) > 1
            else self.ci_pipeline_refs[0]
        )
        return CIStatus(
            state=state,
            url=f"{mr.url}/pipelines",
            jobs=(f"fake-job: {state}",),
            mergeable=self.mergeable,
            merge_detail=self.merge_detail,
            block_reason=self.block_reason,
            sha=sha,
            failed_jobs=failed_jobs,
            pipeline_ref=pipeline_ref,
            cancelled_at=self._next(self.ci_cancelled_at),
            no_checks=self.ci_no_checks,
        )

    async def branch_ci_status(
        self, *, repo: Path, branch: str, head_sha: str = "", pipeline_id: str = ""
    ) -> CIStatus:
        """Same script as `ci_status`, minus the merge request: this is the
        shape `merge_watch` calls once there is no MR left to resolve from
        (Kraft-tsfpk). `head_sha` is accepted only so the fake's signature
        matches the real backends' -- the fake's own sha guard is exercised
        through `ci_shas` regardless of which method a test calls."""
        status = await self.ci_status(
            repo=repo,
            mr=MR(number=0, url="http://fake.forge/branch"),
            branch=branch,
            pipeline_id=pipeline_id,
        )
        if self.branch_ci_states is None:
            return status
        state = self._next(self.branch_ci_states)
        return CIStatus(
            state=state,
            url=status.url,
            jobs=(f"fake-job: {state}",),
            sha=status.sha,
            failed_jobs=status.failed_jobs,
            pipeline_ref=status.pipeline_ref,
            no_checks=status.no_checks,
        )

    async def approval_state(self, *, repo: Path, branch: str) -> ApprovalState:
        return self._next(self.approval_states)

    async def automated_review(
        self, *, repo: Path, branch: str, reviewers: Reviewers = ()
    ) -> ReviewResult:
        # The script answers whether or not a reviewer is configured: a dev
        # instance and a test both script the review they want to walk.
        self.reviewers_asked.append(reviewers)
        result = self._next(self.review_results)
        return ReviewResult(result) if isinstance(result, str) else result

    async def reviewer_feedback(
        self, *, repo: Path, branch: str, reviewers: Reviewers, since: datetime
    ) -> list[ReviewerFeedback]:
        self.reviewers_asked.append(reviewers)
        if self.feedback_script:
            return self._next(self.feedback_script)
        return [
            ReviewerFeedback(r, None)
            if r.bot is not None
            else ReviewerFeedback(
                r,
                None,
                check={"name": r.check, "finished": True, "conclusion": "success", "output": ""},
            )
            for r in reviewers
        ]

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        self.retried.append(ci.url)

    #: Whether `rerun_after_description_change` makes a new pipeline, as GitLab does.
    edit_makes_new_pipeline: bool = False

    async def rerun_after_description_change(
        self, *, repo: Path, branch: str, ci: CIStatus
    ) -> bool:
        self.retried.append(ci.url)
        return self.edit_makes_new_pipeline

    async def set_labels(self, *, repo: Path, branch: str, mr: MR, labels: tuple[str, ...]) -> None:
        self.labels.extend(labels)

    async def merge(self, *, repo: Path, branch: str = "", mr: MR, head: str = "") -> None:
        # number 0 means "resolve from the branch", which is what `run_task`
        # passes and what both real CLIs do. A fake that rejected it
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
        self.merge_heads.append(head)
        if self.forge_head is not None and self.forge_head != head:
            raise HeadMoved(
                f"Head branch was modified (the forge holds {self.forge_head[:12]})",
                current=self.forge_head,
            )
        self.merge_calls.append(number)
        self._asked_head[number] = head
        if self.head_after_merge is not None:
            self.forge_head = self.head_after_merge
        self._dropped.pop(number, None)
        if self.queue_kind != "none" and self.queue_drops:
            self._dropped[number] = self.queue_drops.pop(0)
            return
        if self.merge_delay:
            self._landing[number] = self.merge_delay
            self._queued_head[number] = _head(repo)
        else:
            self.merged.append(number)
            self._merged_head[number] = self.forge_head or _head(repo)

    async def cancel_merge(self, *, repo: Path, branch: str, mr: MRRef) -> None:
        self.cancelled.append(mr.number)
        self._landing.pop(mr.number, None)
        self._queued_head.pop(mr.number, None)

    async def close_mr(self, *, repo: Path, mr: MRRef) -> None:
        if mr.number not in self.closed:
            self.closed.append(mr.number)

    async def reopen_mr(self, *, repo: Path, mr: MRRef) -> None:
        self.reopened.append(mr.number)
        if mr.number in self.closed:
            self.closed.remove(mr.number)

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
        if number in self._landing:
            if self._landing[number]:
                self._landing[number] -= 1
            else:
                del self._landing[number]
                self._merged_head[number] = self.forge_head or self._queued_head.pop(number)
                self._queued_head.pop(number, None)
                self.merged.append(number)
        if number in self.merged:
            state = "merged"
        elif number in self.closed:
            state = "closed"
        else:
            state = "open"
        return MRRef(
            number=number,
            url=f"http://fake.forge/{number}",
            state=state,
            merge_queued=number in self._landing,
            merged_sha=self._merged_head.get(number) or "",
            head_sha=self.forge_head or self._asked_head.get(number, ""),
        )

    async def merge_queue(self, *, repo: Path, branch: str, base: str) -> QueueRead:
        if self.queue_kind == "none":
            return QueueRead()
        numbers = [n for n, b in self.opened.items() if b == branch and self._matches(n, repo)]
        number = max(numbers, default=0)
        if number in self._dropped:
            return self._dropped[number]
        if number in self.merged:
            return QueueRead(kind=self.queue_kind, state="merged")
        if number in self._landing:
            return QueueRead(kind=self.queue_kind, state="queued")
        return QueueRead(kind=self.queue_kind)
