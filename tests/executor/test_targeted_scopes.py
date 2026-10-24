"""A test scope's `targeted_command`: the table that carries it, the diff scope
selection hands back, and the run that uses both."""

import shlex
from pathlib import Path

import pytest
from support.harness import commit_all, entry_of, git, write

from kraft import store
from kraft.executor import dispatch

from .test_scopes import _BUILTIN, _SRC, _UI, _dispatch, _on_a_branch, _selected

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
