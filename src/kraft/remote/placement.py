"""Where a step runs: the pure rules. The hub (`remote/hub.py`) knows who is connected;
this knows what a `runner:` value may be, which setting wins, and what a machine cannot take."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from kraft import store
from kraft.config import RepoEntry
from kraft.remote import registry

LOCAL, ANY = "local", "any"


def runner_for(row, library_runner: str | None, node_id: str) -> str:
    """item node override > item-wide override > library (step > node > chain) > local."""
    node = (store.node_overrides_of(row).get(node_id) or {}).get("runner")
    raw = row["agent_overrides"]
    item = (json.loads(raw) if raw else {}).get("runner")
    return node or item or library_runner or LOCAL


def library_runners(chain) -> set[str]:
    """Every machine (or `any`) the library places a step on; `chain` is a `ResolvedChain` or a
    `MaterializedChain`."""
    resolved = chain.chain if hasattr(chain, "target") else chain
    return {t.runner for n in resolved.nodes for t in n.tasks() if t.runner} - {LOCAL}


def repos_label(repos: Sequence[RepoEntry]) -> str:
    return ", ".join(r.name or Path(r.path).name for r in repos) or "this item"


def allows(worker: registry.Worker, repos: Sequence[RepoEntry]) -> bool:
    if worker.repos is None:
        return True
    return all(any(registry.repo_matches(r, n) for n in worker.repos) for r in repos)


def group_members(runner: str, workers: registry.WorkersFile) -> list[str]:
    """The members of group `runner`: remote machines in name order, then `local`. Empty for a
    name that is no group."""
    return workers.groups().get(runner, [])


def refusal(
    runner: str, *, workers: registry.WorkersFile, repos: Sequence[RepoEntry]
) -> str | None:
    if runner == LOCAL:
        return None
    if runner == ANY:
        if any(allows(w, repos) for w in workers.workers.values()):
            return None
        return (
            f"runner 'any' finds no machine in workers.yaml that may work on {repos_label(repos)}"
        )
    worker = workers.workers.get(runner)
    if worker is None:
        members = group_members(runner, workers)
        if not members:
            return f"runner {runner!r} is not a machine or a group in workers.yaml"
        if LOCAL in members or any(
            allows(workers.workers[m], repos) for m in members if m != LOCAL
        ):
            return None
        return (
            f"group {runner!r} finds no machine in workers.yaml that may work on "
            f"{repos_label(repos)}"
        )
    for r in repos:
        if not allows(worker, [r]):
            return f"machine {runner!r} is not allowed for repo {(r.name or Path(r.path).name)!r}"
    return None


def checkout_problem(repo_path: str | Path, *, members: Sequence[str] = ()) -> str | None:
    """A single bundle carries one repository's commits: not members, submodules or LFS objects."""
    if members:
        return f"it has workspace members ({', '.join(members)}), which a bundle does not carry"
    root = Path(repo_path)
    if (root / ".gitmodules").exists():
        return "the repo has submodules, which a bundle does not carry"
    attrs = root / ".gitattributes"
    if attrs.is_file() and "filter=lfs" in attrs.read_text(errors="replace"):
        return "the repo uses Git LFS, whose objects a bundle does not carry"
    return None


def filing_problems(
    runners: Iterable[str],
    *,
    workers: registry.WorkersFile,
    repos: Sequence[RepoEntry],
    in_place: bool,
    repo_path: str | Path,
    members: Sequence[str] = (),
) -> list[str]:
    problems = []
    for runner in sorted(set(runners) - {LOCAL}):
        if why := refusal(runner, workers=workers, repos=repos):
            problems.append(why)
        elif workers.is_group(runner):
            if why := group_problem(
                runner,
                workers=workers,
                repos=repos,
                in_place=in_place,
                repo_path=repo_path,
                members=members,
            ):
                problems.append(why)
            continue
        if in_place:
            problems.append(
                f"runner {runner!r} cannot run an in-place item: it runs in your own checkout "
                f"(in-place and runner: {runner} cannot be combined)"
            )
        if why := checkout_problem(repo_path, members=members):
            problems.append(f"runner {runner!r} cannot take this item: {why}")
    return problems


def remote_member_can_take(
    runner: str,
    *,
    workers: registry.WorkersFile,
    repos: Sequence[RepoEntry],
    in_place: bool,
    repo_path: str | Path,
    members: Sequence[str] = (),
) -> str | None:
    """None when a remote member of group `runner` could take the item (it allows the repos and
    the item can leave the server); otherwise why it could not."""
    if in_place:
        return "an in-place item"
    if why := checkout_problem(repo_path, members=members):
        return f"this item: {why}"
    if any(allows(workers.workers[m], repos) for m in group_members(runner, workers) if m != LOCAL):
        return None
    return f"{repos_label(repos)}"


def group_problem(
    runner: str,
    *,
    workers: registry.WorkersFile,
    repos: Sequence[RepoEntry],
    in_place: bool,
    repo_path: str | Path,
    members: Sequence[str] = (),
) -> str | None:
    """Why no member of group `runner` could ever take this item, or None: the local worker takes
    anything; a remote member takes what a bundle carries."""
    if LOCAL in group_members(runner, workers):
        return None
    why = remote_member_can_take(
        runner,
        workers=workers,
        repos=repos,
        in_place=in_place,
        repo_path=repo_path,
        members=members,
    )
    if why is None:
        return None
    return (
        f"group {runner!r} has no machine that can take {why}; add the local worker to it "
        "(local: groups:)"
    )


def remote(runner: str | None) -> str | None:
    """What `run_task`'s `runner=` takes: None for `local` and for no placement."""
    return None if runner in (None, LOCAL) else runner


def node_runner(row, node_id: str) -> str:
    """A node's own launches that carry no task (an escalation turn, a review reply)."""
    snapshot = store.materialized_chain_of(row)
    library = None
    if snapshot is not None:
        node = next((n for n in snapshot.chain.nodes if n.id == node_id), None)
        library = node.runner if node is not None else None
    return runner_for(row, library, node_id)
