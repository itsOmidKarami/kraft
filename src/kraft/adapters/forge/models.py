"""The forge's vocabulary: the states, errors and shapes every backend and
every node speaks in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

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
