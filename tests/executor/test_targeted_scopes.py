"""A test scope's `targeted_command`: the table that carries it, the diff scope
selection hands back, and the run that uses both."""

import pytest
from support.harness import commit_all, entry_of, git, write

from kraft import store
from kraft.executor import dispatch

from .test_scopes import _SRC, _UI, _on_a_branch, _selected

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
