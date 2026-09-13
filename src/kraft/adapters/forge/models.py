"""The forge's vocabulary: the states, errors and shapes every backend and
every node speaks in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    # `mr.py` imports `ForgeError`/`MRRef` from here -- a real-time import
    # the other way would be circular. `from __future__ import annotations`
    # already makes every annotation below lazy, so this is type-checking
    # only.
    from kraft.adapters.forge.mr import MRMeta

CIState = Literal["pending", "success", "failed"]


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


class Forge(Protocol):
    async def open_mr(
        self, *, repo: Path, branch: str, title: str, body: str, meta: MRMeta | None = None
    ) -> MR: ...
    async def push(self, *, repo: Path, branch: str) -> None: ...
    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None: ...
    async def ci_status(
        self, *, repo: Path, mr: MR, branch: str, pipeline_id: str = ""
    ) -> CIStatus: ...
    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None: ...
    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None: ...
    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None: ...
    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None: ...


@dataclass
class FakeForge:
    """In-memory forge for tests.

    `ci_states` is consumed one call at a time so a test can script
    pending-then-green without sleeping or polling; the last state repeats
    forever, so a test that only cares about the end state passes one.
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
    #: Title each `open_mr` call was given, keyed by the number returned --
    #: so a test can assert the authored title reached the forge without a
    #: network.
    opened_titles: dict[int, str] = field(default_factory=dict)
    #: `meta` each `open_mr` call was given, same keying as `opened_titles` --
    #: so a test can assert labels/assignees/reviewers reached the forge.
    opened_meta: dict[int, MRMeta] = field(default_factory=dict)
    #: Body each `open_mr` call was given, alongside `bodies` (which only
    #: `update_mr`/`sync_mr` write to).
    opened_bodies: dict[int, str] = field(default_factory=dict)

    async def open_mr(
        self, *, repo: Path, branch: str, title: str, body: str, meta: MRMeta | None = None
    ) -> MR:
        from kraft.adapters.forge.mr import MRMeta

        number = len(self.opened) + 1
        self.opened[number] = branch
        self._opened_repo[number] = str(repo)
        self.opened_titles[number] = title
        self.opened_meta[number] = meta or MRMeta()
        self.opened_bodies[number] = body
        return MR(number=number, url=f"http://fake.forge/{number}")

    async def push(self, *, repo: Path, branch: str) -> None:
        self.pushed.append(branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        self.bodies[branch] = body

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
            sha=sha,
            failed_jobs=failed_jobs,
            pipeline_ref=pipeline_ref,
        )

    async def retry_jobs(self, *, repo: Path, ci: CIStatus) -> None:
        self.retried.append(ci.url)

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
