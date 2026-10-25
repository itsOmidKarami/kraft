"""A test scope's `targeted_command`: the table that carries it, the diff scope
selection hands back, and the run that uses both."""

import shlex
from pathlib import Path

import pytest
from support.harness import commit_all, entry_of, git, write

from kraft import events, executor, store
from kraft.executor import dispatch

from .test_scopes import (
    _BUILTIN,
    _SRC,
    _UI,
    _dispatch,
    _exited,
    _on_a_branch,
    _selected,
    _verify,
)

_WEB_AREA = {
    "web": {
        "paths": ["web/**"],
        "setup": "true",
        "verification": {
            "test_scopes": [
                {"paths": ["web/**"], "command": "web-test", "targeted_command": "web-targeted"}
            ]
        },
    }
}


@pytest.mark.parametrize(
    ("fields", "targeted", "area"),
    [
        pytest.param(
            {
                "test_scopes": [
                    {
                        "paths": ["**"],
                        "command": "just ci-test",
                        "targeted_command": "just  test-targeted",
                    }
                ]
            },
            ["just", "test-targeted"],
            None,
            id="repo-scope",
        ),
        pytest.param({"areas": _WEB_AREA}, ["web-targeted"], "web", id="area-scope"),
        pytest.param({"test_command": "just ci-test"}, None, None, id="bare-test-command"),
    ],
)
def test_the_scope_table_carries_each_scopes_targeted_command(fields, targeted, area):
    [scope] = dispatch._scope_table(entry_of(fields))

    assert (scope["targeted_cmd"], scope["area"]) == (targeted, area)


async def _diff(item_on, repo, change):
    """The diff `_select_scopes` hands back after `change(repo)` on a branch
    cut from a commit that already holds `config/x.yaml`."""
    write(repo, "config/x.yaml", "a: 1\n")
    commit_all(repo)
    it = await _on_a_branch(item_on, repo)
    if change == "no-base":
        await it.database.write(lambda c: store.set_base_ref(c, it.id, "0" * 40))
    else:
        change(repo)
        commit_all(repo)
    _, paths = dispatch._select_scopes(
        it.database, it.id, it.repo, "verify", "verify.main.t", 0, entry_of({"test_scopes": [_SRC]})
    )
    return paths


def _rename(repo):
    (repo / "docs").mkdir(exist_ok=True)
    git(repo, "mv", "config/x.yaml", "docs/x.yaml")


@pytest.mark.parametrize(
    ("change", "paths"),
    [
        pytest.param(_rename, ["config/x.yaml", "docs/x.yaml"], id="rename"),
        pytest.param(lambda r: write(r, "café.py", "x"), ["café.py"], id="non-ascii"),
        pytest.param(lambda r: None, [], id="empty-diff"),
        pytest.param("no-base", None, id="no-diff"),
    ],
)
async def test_select_scopes_lists_the_diff_verbatim_with_both_sides_of_a_rename(
    item_on, repo, change, paths
):
    assert await _diff(item_on, repo, change) == paths


async def test_a_rename_out_of_an_unclaimed_path_fails_selection_open(item_on, repo):
    write(repo, "config/x.yaml", "a: 1\n")
    commit_all(repo)
    it = await _on_a_branch(item_on, repo)
    (repo / "src").mkdir(exist_ok=True)
    git(repo, "mv", "config/x.yaml", "src/x.yaml")
    commit_all(repo)

    assert _selected(it, 0, (_UI, _SRC)) == [("frontend",), ("backend",)]


def _scope(tmp_path, *, targeted=True):
    """A scope whose full command logs `full`, and whose targeted one logs
    `targeted` and keeps what it was handed: the list, its path and the base."""
    scope = {"paths": ["**"], "command": f"sh -c 'echo full >> {tmp_path}/LOG'"}
    if targeted:
        scope["targeted_command"] = (
            f'sh -c \'echo targeted >> {tmp_path}/LOG; cp "$KRAFT_CHANGED_FILES" {tmp_path}/LIST; '
            f'echo "$KRAFT_CHANGED_FILES" > {tmp_path}/PATH; '
            f'echo "$KRAFT_TEST_BASE" > {tmp_path}/BASE\''
        )
    return scope


def _joined(command: str) -> str:
    return shlex.join(shlex.split(command))


_CHANGES = {"src/a.py": "x", "café.py": "x"}
_FULL = {**_BUILTIN, "suite": "full"}


@pytest.mark.parametrize(
    ("task", "changed", "has_targeted", "ran", "reason"),
    [
        pytest.param(_BUILTIN, _CHANGES, True, "targeted", None, id="a-diff"),
        pytest.param(_FULL, _CHANGES, True, "full", dispatch.FULL_BY_SUITE, id="suite-full"),
        pytest.param(_BUILTIN, None, True, "full", dispatch.FULL_NO_DIFF, id="no-diff"),
        pytest.param(_BUILTIN, _CHANGES, False, "full", None, id="no-targeted-command"),
    ],
)
async def test_a_scope_runs_its_targeted_command_in_place_of_the_full_one(
    item_on, tmp_path, task, changed, has_targeted, ran, reason
):
    scope = _scope(tmp_path, targeted=has_targeted)
    full = _joined(scope["command"])

    status, it = await _dispatch(item_on, test_scopes=[scope], task=task, changed=changed)

    assert status == "done"
    assert (tmp_path / "LOG").read_text().split() == [ran]
    expected = _joined(scope["targeted_command"]) if ran == "targeted" else full
    assert [s["command"] for s in it.sessions()] == [expected]
    payload = it.events("test_scopes_selected")[-1]["payload"]
    assert payload["commands"] == [full]
    assert payload["targeted"] == ({full: expected} if ran == "targeted" else {})
    assert payload["full_reason"] == reason
    if ran == "targeted":  # the a-diff row
        assert sorted((tmp_path / "LIST").read_text().splitlines()) == ["café.py", "src/a.py"]
        assert (tmp_path / "BASE").read_text().strip() == git(it.repo, "rev-parse", "HEAD^")
        listed = Path((tmp_path / "PATH").read_text().strip())
        assert listed.is_relative_to(it.run_dirs.base / "items" / it.id)
        assert not listed.is_relative_to(it.repo)


async def _verify_round(it, scope):
    node = it.chain.chain.nodes[0]
    return await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        launch=executor.LaunchContext(
            repo_entry=entry_of({"setup_command": "", "test_scopes": [scope]})
        ),
        round=0,
    )


async def _item_with_an_own_commit(item_on, *, origin=None, stored=None):
    """An item on `main` at its `base_ref` (or `stored`, a commit off that line), one own
    commit on top. With an `origin`, `main` is pushed there before the own commit."""
    it = await item_on(_verify())
    base = git(it.repo, "rev-parse", "HEAD")
    if stored:
        git(it.repo, "checkout", "-q", "-b", "side")
        write(it.repo, "side.txt", "s")
        commit_all(it.repo)
        base = git(it.repo, "rev-parse", "HEAD")
        git(it.repo, "checkout", "-q", "main")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, base))
    if origin:
        git(it.repo, "remote", "add", "origin", str(origin))
        git(it.repo, "push", "-q", "origin", "main")
    write(it.repo, "mine.txt", "x")
    commit_all(it.repo)
    return it, base


def _bare_origin(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    return origin


def _origin_gains_other_txt(tmp_path, repo):
    """`origin/main` gains `other.txt`, fetched into `repo`'s remote-tracking ref."""
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    write(other, "other.txt", "y")
    commit_all(other)
    git(other, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    return git(repo, "rev-parse", "origin/main")


async def test_a_rebased_branch_is_measured_from_where_it_now_forks(item_on, tmp_path):
    it, _ = await _item_with_an_own_commit(item_on, origin=_bare_origin(tmp_path))
    newer = _origin_gains_other_txt(tmp_path, it.repo)
    git(it.repo, "rebase", "-q", "origin/main")

    assert await _verify_round(it, _scope(tmp_path)) == "done"

    assert (tmp_path / "LIST").read_text().splitlines() == ["mine.txt"]
    assert (tmp_path / "BASE").read_text().strip() == newer


async def test_without_a_remote_tracking_ref_the_stored_base_is_kept(item_on, tmp_path):
    it, base = await _item_with_an_own_commit(item_on)

    assert await _verify_round(it, _scope(tmp_path)) == "done"

    assert (tmp_path / "LIST").read_text().splitlines() == ["mine.txt"]
    assert (tmp_path / "BASE").read_text().strip() == base


async def test_a_fork_point_not_past_the_stored_base_keeps_it(item_on, tmp_path):
    it, base = await _item_with_an_own_commit(item_on, origin=_bare_origin(tmp_path), stored=True)
    _origin_gains_other_txt(tmp_path, it.repo)
    git(it.repo, "rebase", "-q", "origin/main")

    assert await _verify_round(it, _scope(tmp_path)) == "done"

    assert (tmp_path / "BASE").read_text().strip() == base


async def test_a_workspace_member_keeps_the_stored_base(item_on, tmp_path):
    it, base = await _item_with_an_own_commit(item_on, origin=_bare_origin(tmp_path))
    _origin_gains_other_txt(tmp_path, it.repo)
    git(it.repo, "rebase", "-q", "origin/main")

    effective = await dispatch._effective_base(it.database, it.row(), it.repo, member=True)

    assert effective == base


async def test_an_empty_diff_after_a_clean_round_runs_the_targeted_command_with_an_empty_list(
    item_on, tmp_path
):
    async def clean_round_zero(it):
        await it.session("r0", "verify.main.t", "done", head_sha=git(it.repo, "rev-parse", "HEAD"))

    status, it = await _dispatch(
        item_on, test_scopes=[_scope(tmp_path)], changed={}, seed=clean_round_zero, round=1
    )

    assert status == "done"
    assert (tmp_path / "LOG").read_text().split() == ["targeted"]
    assert (tmp_path / "LIST").read_text() == ""
    assert it.events("test_scopes_selected")[-1]["payload"]["full_reason"] is None


async def test_the_changed_files_list_is_mounted_read_only_for_a_sandbox(
    item_on, tmp_path, monkeypatch
):
    seen = []
    real = dispatch._subprocess.run_task

    async def spy(*args, **kw):
        seen.append((kw["ro_paths"], kw["env"]))
        return await real(*args, **kw)

    monkeypatch.setattr(dispatch._subprocess, "run_task", spy)

    await _dispatch(item_on, test_scopes=[_scope(tmp_path)], changed=_CHANGES, wid="targeted")
    await _dispatch(
        item_on, test_scopes=[_scope(tmp_path)], task=_FULL, changed=_CHANGES, wid="full"
    )

    [(targeted_ro, targeted_env), (full_ro, full_env)] = seen
    assert targeted_ro == (targeted_env["KRAFT_CHANGED_FILES"],)
    assert full_ro == ()
    assert not [k for k in full_env if k.startswith("KRAFT_")]


_TARGETED_SCOPE = {
    "paths": ["**"],
    "command": "just ci-test",
    "targeted_command": "just test-targeted",
}
_PICKED = {"just ci-test": "just test-targeted"}


async def _pick(it, targeted):
    """A dispatch's picks, as the builtin records them before it runs any scope."""
    payload = {
        "node_id": "verify",
        "hook_point": "verify.main.t",
        "repository": None,
        "round": 0,
        "commands": ["just ci-test"],
        "targeted": targeted,
    }
    await it.database.write(lambda c: events.append(c, it.id, "test_scopes_selected", payload))


@pytest.mark.parametrize(
    ("scope", "picked", "ran", "expected"),
    [
        pytest.param(
            _TARGETED_SCOPE,
            {},
            "just test-targeted",
            {"identity": "just ci-test", "targeted": True, "scope": "**"},
            id="targeted",
        ),
        pytest.param(
            _TARGETED_SCOPE,
            {},
            "just ci-test",
            {"identity": "just ci-test", "scope": "**"},
            id="full",
        ),
        pytest.param(
            {"paths": ["**"], "command": "just ci-test"},
            _PICKED,
            "just test-targeted",
            {"identity": "just ci-test", "targeted": True, "scope": "**"},
            id="dropped-from-config",
        ),
        pytest.param(
            None,
            _PICKED,
            "just test-targeted",
            {"identity": "just ci-test", "targeted": True},
            id="unreadable-table",
        ),
    ],
)
async def test_scope_results_reads_a_session_as_its_scope(item_on, scope, picked, ran, expected):
    it = await item_on(_verify(), repo="/r")
    await _exited(it, [("s1", "done", ran, None)])
    if picked:
        await _pick(it, picked)
    entry = entry_of({"setup_command": "", "test_scopes": [scope]}) if scope else None

    found = dispatch.scope_results(it.database, it.id, "verify", "verify.main.t", entry)

    assert found == [{"command": ran, **expected, "passed": True, "session_id": "s1"}]


async def test_scope_results_counts_a_scope_once_whichever_command_ran(item_on):
    it = await item_on(_verify(), repo="/r")
    await _exited(
        it,
        [("s1", "failed", "just ci-test", None), ("s2", "done", "just test-targeted", None)],
    )
    entry = entry_of({"setup_command": "", "test_scopes": [_TARGETED_SCOPE]})

    found = dispatch.scope_results(it.database, it.id, "verify", "verify.main.t", entry)

    assert [(e["session_id"], e["identity"], e["passed"]) for e in found] == [
        ("s2", "just ci-test", True)
    ]


def _logs(tmp_path) -> dict:
    """A scope that records that it ran."""
    return {"paths": ["**"], "command": f"sh -c 'echo ran >> {tmp_path}/LOG'"}


async def _round(item_on, fields, *, task=_BUILTIN):
    """Dispatch the builtin once, on a branch that changed `web/x.txt`."""
    it = await item_on(_verify(task))
    base = git(it.repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, base))
    write(it.repo, "web/x.txt", "x")
    commit_all(it.repo)
    node = it.chain.chain.nodes[0]
    status = await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        launch=executor.LaunchContext(repo_entry=entry_of({"setup_command": "", **fields})),
        round=0,
    )
    return status, it


@pytest.mark.parametrize(
    ("fields", "written"),
    [
        pytest.param(
            lambda t: {"test_scopes": [_logs(t), {"paths": ["**"], "command": "cd sub && true"}]},
            "cd sub && true",
            id="command",
        ),
        pytest.param(
            lambda t: {"test_scopes": [{**_logs(t), "targeted_command": "a && b"}]},
            "a && b",
            id="targeted-command",
        ),
        pytest.param(
            lambda t: {
                "test_scopes": [_logs(t)],
                "areas": {
                    "web": {
                        "paths": ["web/**"],
                        "setup": "cd web && npm ci",
                        "verification": {
                            "test_scopes": [{"paths": ["web/**"], "command": "web-test"}]
                        },
                    }
                },
            },
            "cd web && npm ci",
            id="area-setup",
        ),
    ],
)
async def test_a_scope_command_written_for_a_shell_stops_the_round(
    item_on, tmp_path, fields, written
):
    status, it = await _round(item_on, fields(tmp_path))

    assert status == "config_error"
    assert not (tmp_path / "LOG").exists()
    [session] = it.sessions()
    log = Path(session["log_path"]).read_text()
    assert written in log and "runs without a shell" in log and "sh -c '" in log


async def test_the_full_suite_never_runs_a_targeted_command_so_never_refuses_it(item_on, tmp_path):
    scope = {**_logs(tmp_path), "targeted_command": "a && b"}

    status, _ = await _round(item_on, {"test_scopes": [scope]}, task={**_BUILTIN, "suite": "full"})

    assert status == "done"
    assert (tmp_path / "LOG").read_text().split() == ["ran"]
