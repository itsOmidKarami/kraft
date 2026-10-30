"""`kraft.adapters.agent.run_agent_task` for an in-place item, which runs in a
person's own checkout: what its agent is told, and the take, mark and check
an agent outside the walk (an escalation, reply or gate review) runs around
itself, as `dispatch` does around a walk task, or the copy of the branch a
read-only one runs in instead (`in_copy`)."""

from pathlib import Path

import pytest
from support.harness import IN_PLACE, git, v1_node, write

from kraft import inplace, store
from kraft.adapters import agent

from .test_agent import PY_FAKE, _system_prompt


def test_an_in_place_agent_is_told_whose_checkout_it_is(run, in_place):
    prompt = _system_prompt(run(**in_place)["cmd"])
    for phrase in (
        "You are working in a person's own checkout, on this work item's branch `kraft/w1`.",
        "`git --literal-pathspecs add -- <paths>`, then "
        '`git --literal-pathspecs commit -m "..." -- <paths>`',
        "Never `git add -A`, `git add .`, `git commit -a`, `git stash`, `git checkout`, "
        "`git switch` or `git reset`",
        "Do not push and do not merge",
        "Exception: `.engineering/`",
    ):
        assert phrase in prompt
    assert "destroyed with the worktree" not in prompt
    assert "git worktree cut for this work item" not in prompt
    assert prompt.count("Commit everything you change before you exit") == 1


@pytest.mark.parametrize(
    ("script", "stop"),
    [
        (
            "echo agent >> calc.py && git add -- calc.py && git commit -qm agent -- calc.py",
            "the agent committed calc.py, which already had uncommitted changes",
        ),
        ("git checkout -q main", "the checkout moved to main during the run of escalation in "),
        ("echo x > left.txt", None),
    ],
    ids=["overlap", "moved", "leftover"],
)
async def test_an_in_place_session_outside_the_walk_takes_marks_and_checks_its_checkout(
    item_on, monkeypatch, script, stop
):
    """An escalation agent, or a gate reviewer that decides, runs the steps a
    walk task does: the checkout switched to the item's branch, marked, checked
    after. A reply agent and a checks-first reviewer read a copy instead."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(it.repo, "branch", branch)
    write(it.repo, "calc.py", "# the person's edit\n")
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", f"git symbolic-ref HEAD > .git/on && {script}")

    status = await _outside_the_walk(it)

    assert status == "done"
    assert (it.repo / ".git" / "on").read_text().strip() == f"refs/heads/{branch}"
    [marked] = it.events("task_marked")
    assert (marked["payload"]["task"], marked["payload"]["dirty"]) == ("escalation", ["calc.py"])
    needs_human = it.events("work_item_needs_human")
    if stop:
        assert it.row()["stop_kind"] == "checkout"
        reason = needs_human[-1]["payload"]["reason"]
        assert stop in reason
        assert "retry the item" in reason, "names a verb a checkout stop refuses"
    else:
        assert needs_human == []
        [left] = it.events("left_uncommitted")
        assert left["payload"]["paths"] == ["left.txt"]


async def test_an_in_place_session_refused_its_checkout_never_launches(item_on, monkeypatch):
    """The branch is gone: nothing switches, the agent never runs, and the
    refusal is the session's log for the caller to stop or skip on."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", "touch ran")

    with pytest.raises(inplace.CheckoutRefused, match="is gone"):
        await _outside_the_walk(it)

    assert not (it.repo / "ran").exists()
    [session] = it.sessions()
    assert session["status"] == "config_error"
    assert "is gone" in Path(session["log_path"]).read_text()
    assert it.events("task_marked") == []


@pytest.mark.parametrize(
    ("busy", "in_copy"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["holds-it", "another-item-uses-it", "in-copy", "in-copy-another-item-uses-it"],
)
async def test_a_sessions_own_record_waits_for_its_session(item_on, monkeypatch, busy, in_copy):
    """`on_session` runs once the session is written and holds the checkout,
    before the agent, and `after_checkout` once the checkout is taken and
    marked, or its copy made; neither for one refused it, which started nothing.
    A launch in a copy still takes the lease: it waits for another item as any does."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(it.repo, "branch", branch)
    if busy:
        await item_on([v1_node()], wid="a", target=IN_PLACE, title="A")
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    copy = inplace.copy_path(it.run_dirs, it.id, "s1")
    seen = []

    async def on_session():
        seen.append(len(it.sessions()))

    async def after_checkout():
        seen.append(
            (
                len(it.events("task_marked")),
                git(it.repo, "symbolic-ref", "HEAD"),
                (copy / ".git").is_dir(),
            )
        )

    hooks = {"on_session": on_session, "after_checkout": after_checkout}
    if in_copy:
        hooks |= {"in_copy": True, "read_only": True, "cwd": copy}
    if busy:
        with pytest.raises(store.CheckoutBusy):
            await _outside_the_walk(it, **hooks)
    else:
        assert await _outside_the_walk(it, **hooks) == "done"

    taken = (0, "refs/heads/main", True) if in_copy else (1, f"refs/heads/{branch}", False)
    assert seen == ([] if busy else [1, taken])
    assert (copy / ".git").is_dir() == (in_copy and not busy), "refused: no copy made"


async def test_a_session_in_a_copy_never_borrows_the_checkout(item_on, monkeypatch, tmp_path):
    """In a copy of the branch tip, not the person's checkout: nothing
    switches it, marks it or checks it."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(it.repo, "checkout", "-q", "-b", branch)
    git(it.repo, "commit", "-q", "--allow-empty", "-m", "the item's own")
    tip = git(it.repo, "rev-parse", "HEAD")
    git(it.repo, "checkout", "-q", "main")
    head = tmp_path / "head"
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", f"git rev-parse HEAD > {head}")

    status = await _outside_the_walk(
        it, cwd=inplace.copy_path(it.run_dirs, it.id, "s1"), in_copy=True, read_only=True
    )

    assert status == "done"
    assert head.read_text().strip() == tip
    assert git(it.repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert it.events("checkout_switched") == []
    assert inplace.load_mark(it.run_dirs, it.id, "escalation") is None


async def test_a_copy_that_fails_ends_the_session_and_raises(item_on, monkeypatch, tmp_path):
    """The branch is gone: no copy, the agent never runs, nothing is created
    in the person's repository, and the session ends `config_error`."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    ran = tmp_path / "ran"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", f"touch {ran}")

    with pytest.raises(inplace.CopyFailed, match="could not make a copy"):
        await _outside_the_walk(
            it, cwd=inplace.copy_path(it.run_dirs, it.id, "s1"), in_copy=True, read_only=True
        )

    assert not ran.exists()
    [session] = it.sessions()
    assert session["status"] == "config_error"
    assert "could not make a copy" in Path(session["log_path"]).read_text()
    branch = f"refs/heads/{store.branch_for(it.row())}"
    assert git(it.repo, "for-each-ref", branch) == ""


@pytest.mark.parametrize(
    ("harness", "probe", "kw"),
    [
        ("opencode", "_cli_version", {}),
        ("codex", "_hook_install.codex_hook_flags", {"deny_tools": ("Bash",)}),
    ],
    ids=["version-probe", "codex-hook-probe"],
)
async def test_a_copy_launchs_cli_probes_run_outside_the_empty_copy(
    item_on, monkeypatch, harness, probe, kw
):
    """The copy is empty until the lease is taken, and is refused if it is not:
    a CLI asked something before then, which may leave a file where it runs, is
    asked in the session's own folder, never the copy."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    git(it.repo, "branch", store.branch_for(it.row()))
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    copy = inplace.copy_path(it.run_dirs, it.id, "s1")
    asked = []

    async def probe_writing_its_cwd(exe, name, cwd, runner=None):
        asked.append(Path(cwd))
        (Path(cwd) / "probed").write_text("")
        return () if harness == "codex" else None

    monkeypatch.setattr(f"kraft.adapters.agent.{probe}", probe_writing_its_cwd)

    status = await _outside_the_walk(
        it, harness=harness, cwd=copy, in_copy=True, read_only=True, **kw
    )

    assert status == "done"
    assert (copy / ".git").is_dir()
    assert asked == [copy.parent]
    assert not (copy / "probed").exists()


def _outside_the_walk(it, **kw):
    return agent.run_agent_task(
        it.database,
        it.run_dirs,
        session_id="s1",
        work_item_id=it.id,
        node_id="build",
        hook_point="escalation",
        command=PY_FAKE,
        title="t",
        task_instruction="look again",
        repo_path=str(it.repo),
        cwd=kw.pop("cwd", it.repo),
        takes_checkout=True,
        **kw,
    )
