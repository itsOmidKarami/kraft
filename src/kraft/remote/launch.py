"""What `run_task` does to hand a step to a machine: wait for a slot, describe the job."""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from dataclasses import asdict

from kraft import caps, events, ids, store
from kraft.config import RepoEntry
from kraft.remote import local_link, placement, protocol
from kraft.remote import runs as runs_mod
from kraft.remote.hub import Claim, PlacementRefused
from kraft.remote.jobs import JobSpec
from kraft.vocab.events import RemoteEvent

#: How often a step that cannot park looks at its group again, as `Hub.claim` does.
POLL_S = 0.5


def release(claim: Claim) -> None:
    """Give back a claim that never reached `Runs.launch`: its hub slot, and the machine's slot
    held for it. Idempotent."""
    runs = runs_mod.current()
    if runs is not None:
        runs.hub.release(claim)
        runs.unreserve_soon(claim.machine, claim.key)


class PlaceAgain(Exception):
    """The machine a group step was given cannot take it (it lacks a capability the launch turned
    out to need). Carries `needs`; `Placement.choose(needs)` places the step again."""

    def __init__(self, needs: Mapping[str, str]) -> None:
        super().__init__("this step needs a capability the machine it was given lacks")
        self.needs = needs


class Placement:
    """Where a step whose runner is a group runs, chosen by `choose`, which **takes the slot**: a
    remote member's (`held`, reserved on the machine) or the local worker's (`held_local`). The
    launch uses it instead of claiming; `release` gives back what the launch did not take over.
    `runner` is the group, a pinned machine or `"local"`; `wait` says why nothing is free
    (only for a step that can park); `refusal`, that no member can ever take it."""

    def __init__(
        self,
        db,
        row,
        node_id: str,
        hook_point: str,
        group: str,
        repos: Sequence[RepoEntry],
        *,
        session_key: str,
        resume: bool,
        can_park: bool,
        pin_node: bool = False,
    ) -> None:
        self._db, self._row, self._node_id, self._hook_point = db, row, node_id, hook_point
        self._pin_node = pin_node
        self._group, self._repos, self._key = group, list(repos), session_key
        self._resume, self._can_park = resume, can_park
        self.runner = group
        self.held: Claim | None = None
        self.held_local: str | None = None
        self.wait: str | None = None
        self.refusal: str | None = None
        self.waited_s = 0.0

    def release(self) -> None:
        """Give back what is held. A claim a launch handed to `Runs` stays with it. Idempotent."""
        held, self.held = self.held, None
        if held is not None:
            runs = runs_mod.current()
            if runs is None or not runs.owns(held.key):
                release(held)
        local, self.held_local = self.held_local, None
        if local is not None and (link := local_link.current()) is not None:
            link.release(local)

    def _reset(self) -> None:
        self.release()
        self.runner, self.wait, self.refusal = self._group, None, None

    async def choose(self, needs: Mapping[str, str] | Collection[str] = ()) -> None:
        """Take a slot on a member. A step that can park comes back with `wait` set when none is
        free; one that cannot waits here, saying so once, and takes whichever side frees first."""
        self._reset()
        runs = runs_mod.current()
        if runs is None:
            self.refusal = f"runner {self._group!r} needs machines, and this process has none"
            return
        began, said = caps.monotonic(), None
        try:
            while True:
                self.wait = self.refusal = None
                if await self._try(runs, needs):
                    break
                if self.refusal or (self._can_park and self.wait):
                    break
                if self.wait != said:
                    await _note(
                        self._db, self._row["id"], self._node_id, self._hook_point, self.wait
                    )
                    said = self.wait
                await asyncio.sleep(POLL_S)
        except BaseException:
            self.release()
            raise
        self.waited_s = caps.monotonic() - began if self.held or self.held_local else 0.0

    def _pin(self, members: list[str]) -> str | None:
        """The member a resumed session must go back to: where its state lives."""
        if not self._resume:
            return None
        found = self._db.read(
            lambda c: store.pinned_runner(
                c, self._row["id"], self._node_id, None if self._pin_node else self._hook_point
            )
        )
        return found if found in members else None

    async def _try(self, runs, needs) -> bool:
        """One look at the group. True with a slot held; otherwise `wait` or `refusal` says why."""
        workers = runs.hub.file
        members = placement.group_members(self._group, workers)
        if not members:
            self.refusal = f"runner {self._group!r} is not a machine or a group in workers.yaml"
            return False
        link = local_link.current()
        pinned = self._pin(members)
        remote_ok, local_ok = self._where(workers, members)
        if pinned is not None:
            remote_ok, local_ok = remote_ok and pinned != "local", pinned == "local" and local_ok
        if not (remote_ok or local_ok):
            self.refusal = placement.group_problem(
                self._group,
                workers=workers,
                repos=self._repos,
                in_place=store.is_in_place(self._row),
                repo_path=self._row["repo"],
                members=self._mounts(),
            ) or (
                f"group {self._group!r} finds no machine in workers.yaml that may work on "
                f"{placement.repos_label(self._repos)}"
            )
            return False
        if remote_ok:
            runner = pinned or self._group
            exclude: set[str] = set()
            while True:
                try:
                    got = await runs.hub.claim(
                        runner, self._repos, self._key, needs=needs, wait=False, exclude=exclude
                    )
                except PlacementRefused as exc:
                    if not local_ok:
                        self.refusal = str(exc)
                        return False
                    break
                if got is None:
                    break
                if await self._reserved(runs, got):
                    self.held, self.runner = got, runner if pinned else self._group
                    return True
                exclude.add(got.machine)
        if local_ok and link is not None and link.connected and link.take(self._key):
            self.held_local, self.runner = self._key, placement.LOCAL
            return True
        self.wait = f"waiting for {pinned}" if pinned else f"waiting for a machine in {self._group}"
        return False

    def _mounts(self) -> list[str]:
        from kraft import builtins  # `builtins` imports adapters.subprocess

        return builtins.item_mounts(self._row)

    def _where(self, workers, members: list[str]) -> tuple[bool, bool]:
        """`(a remote member could take the item, the local worker could)`."""
        remote = placement.remote_member_can_take(
            self._group,
            workers=workers,
            repos=self._repos,
            in_place=store.is_in_place(self._row),
            repo_path=self._row["repo"],
            members=self._mounts(),
        )
        return remote is None, placement.LOCAL in members

    async def _reserved(self, runs, got: Claim) -> bool:
        """Whether the machine agreed to hold the slot; if it turned the step away, the claim is
        given back and the machine left alone until it reports a free slot."""
        if protocol.RESERVE not in runs.hub.caps(got.machine):
            return True
        try:
            answer = await runs.reserve(got.machine, self._key)
        except BaseException:
            release(got)
            raise
        if answer.ok:
            return True
        runs.hub.release(Claim(got.machine, self._key))
        await runs.unreserve(got.machine, self._key)
        if answer.capacity is not None:
            runs.hub.set_machine_capacity(got.machine, answer.capacity)
        runs.hub.mark_full(got.machine)
        return False


async def _note(db, work_item_id: str, node_id: str, hook_point: str, reason: str) -> None:
    """Say on the board that a step is waiting, for the walk that cannot park."""
    payload = {"task": hook_point, "node_id": node_id, "reason": reason}
    await db.write(
        lambda c: events.append(c, work_item_id, RemoteEvent.STEP_WAITING, payload, node_id=node_id)
    )


@contextlib.asynccontextmanager
async def placed(
    db,
    row,
    node_id: str,
    hook_point: str,
    runner: str,
    repos: Sequence[RepoEntry],
    *,
    session_key: str,
    resume: bool,
    can_park: bool,
    needs: Mapping[str, str] | Collection[str] = frozenset(),
    pin_node: bool = False,
) -> AsyncIterator[Placement]:
    """Place a step whose `runner` is a group around its launch, and give back whatever the
    launch did not take over, however it ends. See `Placement`. `pin_node`: a resumed step goes
    back to where any task of its node last ran (an escalation turn, a review reply)."""
    p = Placement(
        db, row, node_id, hook_point, runner, repos,
        session_key=session_key, resume=resume, can_park=can_park, pin_node=pin_node,
    )  # fmt: skip
    try:
        await p.choose(needs)
        yield p
    finally:
        p.release()


def extend(time_cap: caps.Deadline | None, waited_s: float) -> caps.Deadline | None:
    """`time_cap` moved on by the seconds a step waited for its slot."""
    if time_cap is None or waited_s <= 0.05:
        return time_cap
    return caps.Deadline(time_cap.at + waited_s, time_cap.hit)


def is_group(runner: str | None) -> bool:
    runs = runs_mod.current()
    return runner is not None and runs is not None and runs.hub.file.is_group(runner)


async def run_placed(
    db,
    row,
    node_id: str,
    hook_point: str,
    runner: str | None,
    repos: Sequence[RepoEntry],
    *,
    session_id: str,
    resume: bool,
    attempt,
    pin_node: bool = False,
):
    """`await attempt(runner, claim, waited_s)` for a launch that cannot park (an escalation turn,
    a review): placed on a group's member first when `runner` names a group, and placed again
    when `attempt` raises `PlaceAgain`. Any other runner goes straight through."""
    if not is_group(runner):
        return await attempt(runner, None, 0.0)
    async with placed(
        db, row, node_id, hook_point, runner, repos,
        session_key=ids.session_ref(row["id"], session_id),
        resume=resume, can_park=False, pin_node=pin_node,
    ) as p:  # fmt: skip
        while True:
            try:
                return await attempt(placement.remote(p.runner), p.held, p.waited_s)
            except PlaceAgain as again:
                await p.choose(again.needs)


async def claim(
    db,
    *,
    work_item_id,
    session_id,
    node_id,
    runner,
    repo_entry,
    time_cap,
    needs: Mapping[str, str] | Collection[str] = (),
    held: Claim | None = None,
):
    """`(claim, refusal, time_cap)`. `held` is a slot `placed` already took and reserved: it is
    returned as it is. A refusal is text for the session's log; a wait is a
    cancellable `await` here, with `time_cap` moved on by however long it lasted. A machine that
    listed `reserve` is asked to hold a slot for the step before this returns: one at its own
    limit turns the step away, and the claim goes on to another machine, or waits for it. Whatever
    ends this call without a claim has given back everything it took."""
    if held is not None:
        return held, None, time_cap
    runs = runs_mod.current()
    if runs is None:
        return None, f"runner {runner!r} needs machines, and this process has none", time_cap
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    repos = [repo_entry] if repo_entry is not None else []
    from kraft import builtins  # `builtins` imports adapters.subprocess

    problems = placement.filing_problems(
        [runner],
        workers=runs.hub.file,
        repos=repos,
        in_place=bool(row is not None and store.is_in_place(row)),
        repo_path=row["repo"] if row is not None else ".",
        members=builtins.item_mounts(row) if row is not None else [],
    )
    if problems:
        return None, problems[0], time_cap
    pinned = (
        db.read(lambda c: store.pinned_machine(c, work_item_id, node_id))
        if runner == placement.ANY
        else None
    )
    key = ids.session_ref(work_item_id, session_id)
    began = caps.monotonic()
    got = None
    try:
        while True:
            try:
                got = await runs.hub.claim(runner, repos, key, pinned=pinned, needs=needs)
            except PlacementRefused as exc:
                return None, str(exc), time_cap
            if protocol.RESERVE not in runs.hub.caps(got.machine):
                break  # a machine with no limit of its own is not asked
            answer = await runs.reserve(got.machine, key)
            if answer.ok:
                break
            machine, got = got.machine, None
            runs.hub.release(Claim(machine, key))
            await runs.unreserve(machine, key)
            if answer.capacity is not None:  # it answered: it is at its own limit
                runs.hub.set_machine_capacity(machine, answer.capacity)
                runs.hub.mark_full(machine)
    except BaseException:
        if got is not None:
            release(got)
        raise
    waited = caps.monotonic() - began
    if time_cap is not None and waited > 0.05:
        time_cap = caps.Deadline(time_cap.at + waited, time_cap.hit)
    return got, None, time_cap


def build(
    *,
    work_item_id,
    session_id,
    node_id,
    hook_point,
    cmd,
    cwd,
    env,
    repo_entry,
    branch,
    base,
    identity,
    run_dirs,
    entries,
    time_cap: caps.Deadline | None,
    idle_s: float | None,
    sandbox: dict | None = None,
    egress: dict | None = None,
    credentials: list[dict] | None = None,
    sentinels: dict[str, str] | None = None,
    withheld: list[str] | None = None,
    setup_sandbox: dict | None = None,
    codex_hook: dict | None = None,
) -> JobSpec:
    done = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"branch {branch} is not in {cwd}, so there is nothing to send to a machine"
        )
    rw = [e for e in entries if e.rw]
    return JobSpec(
        work_item_id=work_item_id,
        session_id=session_id,
        node_id=node_id,
        hook_point=hook_point,
        argv=list(cmd),
        cwd=str(cwd),
        env=dict(env),
        repo_env=dict(repo_entry.env) if repo_entry is not None else {},
        passthrough=list(repo_entry.env_passthrough) if repo_entry is not None else [],
        setup_command=repo_entry.setup_command if repo_entry is not None else None,
        branch=branch,
        head=done.stdout.strip(),
        base=base or done.stdout.strip(),
        identity=dict(identity),
        server_run_dir=str(run_dirs.base),
        server_worktree=str(cwd),
        dirs=[str(e.path) for e in rw if e.path.is_dir()],
        rw=[str(e.path) for e in rw],
        # Enforced on the machine as well: while the server is asleep nothing else would.
        deadline_s=None if time_cap is None else max(0.0, time_cap.at - caps.monotonic()),
        cap=None if time_cap is None else asdict(time_cap.hit),
        idle_s=idle_s,
        sandbox=sandbox,
        egress=egress,
        credentials=list(credentials or []),
        sentinels=dict(sentinels or {}),
        withheld=list(withheld or []),
        setup_sandbox=setup_sandbox,
        ro=[str(e.path) for e in entries if not e.rw],
        codex_hook=codex_hook,
    )
