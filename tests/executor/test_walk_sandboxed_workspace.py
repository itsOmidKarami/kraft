"""One sandboxed workspace item through the walk, end to end (Kraft-ju36l):
a worker commits in the member and in the root from inside its sandbox, then
the chain publishes both repositories, the member first, and bumps the root's
pointer to what merged. That nothing a worker plants runs is the key test's
(`worker/test_host_git_trust.py`) and the WP3 e2e's to pin, not this one's.

`fake` runs the worker on the host through the fake `docker`, which mounts
nothing: the script moves each commit into its ref store itself, as git in a
container would have written it there. `docker` and `podman` run it for real.
"""

import os
import shlex
from pathlib import Path

import pytest
from support.harness import entry_of, fake_docker_bin
from support.sandbox_image import build_git_image
from support.workspace import repositories
from templates.test_workspace_publication import (
    _PUBLISH,
    _git_out,
    _LandingForge,
    _publishable,
    _repos,
)

from kraft import events, executor, store
from kraft.adapters import forge
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy, TemplatePolicyOverride
from kraft.worker import refstore
from kraft.worker.sandbox import linked_gitdirs, member_gitdirs

_REL = "repos/pkg"
_ID = "-c user.name=t -c user.email=t@t"


@pytest.fixture(
    params=[
        "fake",
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def runtime(request, tmp_path, monkeypatch):
    """`(name, image)`: the fake `docker` on PATH, or a real runtime's git image."""
    if request.param == "fake":
        monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
        return "fake", "kraft-worker:fake"
    return request.param, build_git_image(request.param, tmp_path, monkeypatch)


def _stopped(database, row) -> str:
    """Why the walk stopped, and what the worker said: a real runtime's
    failure is in its session's log, not in the status."""
    seen = database.read(lambda c: events.read_after(c, 0, row["id"]))
    stops = [e["payload"]["reason"] for e in seen if e["type"] == "work_item_needs_human"]
    logs = database.read(
        lambda c: [
            r["log_path"]
            for r in c.execute(
                "SELECT log_path FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at",
                (row["id"],),
            )
        ]
    )
    said = [Path(p).read_text()[-500:] for p in logs if Path(p).exists()]
    return f"stopped: {stops}; worker logs: {said}"


def _script(wt: Path, branch: str, into_stores: list) -> str:
    """The worker: a member commit, then a root commit over the moved gitlink.
    `into_stores` is `(repo, head before, store)` for the fake runtime; a real
    one's worker moves each repository's `main` too, which only its ref store
    sees."""
    q = shlex.quote
    lines = [
        "set -e",
        f"cd {q(str(wt / _REL))}",
        "echo 'x = 1' > lib.py",
        f"git add -A && git {_ID} commit -qm 'member work'",
        f"cd {q(str(wt))}",
        f"echo root > root.txt && git add -A && git {_ID} commit -qm 'root work'",
    ]
    if not into_stores:
        lines += [f"git -C {q(str(r))} update-ref refs/heads/main HEAD" for r in (wt / _REL, wt)]
    for repo, before, shadow in into_stores:
        ref = shadow / "refs" / "heads" / branch
        lines += [
            f"new=$(git -C {q(str(repo))} rev-parse HEAD)",
            f"git -C {q(str(repo))} update-ref refs/heads/{branch} {before}",
            f"mkdir -p {q(str(ref.parent))} && echo $new > {q(str(ref))}",
        ]
    return "\n".join(lines)


async def test_a_sandboxed_workspace_item_walks_to_merged_with_its_pointer_bumped(
    database, run_dirs, tmp_path, monkeypatch, runtime
):
    name, image = runtime
    policy = InstancePolicy.from_input(InstancePolicyInput()).apply_template_override(
        TemplatePolicyOverride(sandbox=SandboxPolicy(kind="docker", image=image))
    )
    worker = {"id": "w", "kind": "subprocess", "command": "sh worker.sh"}
    work = {"id": "work", "kind": "exec", "tasks": [worker]}
    row, wt, origin = await _publishable(
        database,
        run_dirs,
        tmp_path,
        pointer="bump",
        untouched=("pkg",),
        nodes=[work, *_PUBLISH],
        effective_policy=policy,
        repository_policies={"ws": policy, "pkg": policy},
    )
    wt, m = wt.resolve(), tmp_path / "pkg-connected"
    branch = store.branch_for(row)
    stores = [(wt / _REL, member_gitdirs(m, wt, _REL)[1]), (wt, linked_gitdirs(wt)[1])]
    fake = [
        (repo, _git_out(repo, "rev-parse", "HEAD"), refstore.shadow_dir(run_dirs.base, admin))
        for repo, admin in stores
    ]
    script = _script(wt, branch, fake if name == "fake" else [])
    (wt / "worker.sh").write_text(script)
    mains = {r: _git_out(r, "rev-parse", "main") for r in (m, Path(row["repo"]))}
    landing = _LandingForge()
    monkeypatch.setattr(forge.run, "resolve", lambda _name: landing)

    status = None
    for _ in range(4):
        await database.write(lambda c: store.mark_reentered(c, row["id"]))
        status = await executor.run(
            database,
            run_dirs,
            work_item_id=row["id"],
            policy=None,
            launch=LaunchContext(
                repo_entry=entry_of({"setup_command": "", "forge": "github"}),
                repositories=repositories(tmp_path, "pkg"),
            ),
        )
        if status != "waiting":
            break

    assert status == "completed", _stopped(database, row)
    assert _repos(database, row) == {"root": "merged", "submodule": "merged"}
    assert [e for e in landing.order if e[0] == "merge"] == [("merge", "pkg"), ("merge", wt.name)]
    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    # Each branch reached its origin and landed there, the worker's commits in it.
    assert "member work" in _git_out(tmp_path / "pkg", "log", "--format=%s", "main")
    assert "root work" in _git_out(origin, "log", "--format=%s", "main")
    assert _git_out(origin, "rev-parse", "main:repos/pkg") == merged
    assert {r: _git_out(r, "rev-parse", "main") for r in mains} == mains
