"""`run_task` pins each repo's forge account and backend at the item's first
forge node, and stops the item when a pin no longer holds."""

from __future__ import annotations

import pytest

from kraft import store
from kraft.adapters import forge
from kraft.config import RepoEntry
from kraft.forges import ForgeAccount, ForgesFile

_GH = ForgeAccount(forge="github", web_url="https://github.com")
_GL = ForgeAccount(forge="gitlab", web_url="https://gitlab.example")


@pytest.mark.parametrize(
    "first, second, stop, pins_after",
    [
        pytest.param(
            ({"a": _GH}, "a"),
            None,
            None,
            {"": {"account": "a", "backend": "gh"}},
            id="first-node-pins-root",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            ({"a": _GH}, "a"),
            None,
            {"": {"account": "a", "backend": "gh"}},
            id="unchanged-runs",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            ({}, None),
            "forge account a was removed",
            {"": {"account": "a", "backend": "gh"}},
            id="account-removed-stops",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            ({"a": _GL}, "a"),
            "forge account a now says forge: gitlab, was github",
            {"": {"account": "a", "backend": "gh"}},
            id="forge-changed-stops",
        ),
        pytest.param(
            ({}, "ghost"),
            None,
            "forge_account ghost names no account in forges.yaml",
            {},
            id="unknown-account-at-run-stops",
        ),
        pytest.param(
            ({}, None),
            ({"a": _GH}, None),
            None,
            {"": {"account": None, "backend": "gh"}},
            id="null-pin-never-stops",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            # an invalid forges.yaml keeps the running config, so the same accounts arrive
            ({"a": _GH}, "a"),
            None,
            {"": {"account": "a", "backend": "gh"}},
            id="invalid-file-keeps-pin",
        ),
    ],
)
async def test_the_first_forge_node_pins_and_a_broken_pin_stops_the_item(
    run_forge, database, tmp_path, first, second, stop, pins_after
):
    def run(accounts, named, sid):
        entry = RepoEntry(path=str(tmp_path), forge="github", forge_account=named)
        return run_forge(
            forge.FakeForge(),
            "open_mr",
            sid,
            backend="auto",
            repo_forge="github",
            repo_entry=entry,
            repositories={},
            forges=ForgesFile(accounts=accounts),
        )

    result = await run(*first, "p1")
    if second is not None:
        result = await run(*second, "p2")
    sid = "p2" if second is not None else "p1"

    if stop is None:
        assert result == ("done", "done")
    else:
        assert result == ("config_error", "config_error")
        assert stop in run_forge.log(sid)
        if "forge_account" not in stop:
            assert "kraft item retry" in run_forge.log(sid)
    assert database.read(lambda c: store.forge_pins(c, "w1")) == pins_after


async def test_a_member_is_pinned_by_its_mount(run_forge, database, tmp_path, monkeypatch):
    monkeypatch.setattr(forge.run._builtins, "item_members", lambda row: [("libs/core", "lib")])
    root = RepoEntry(path=str(tmp_path), forge="github")
    member = RepoEntry(path=str(tmp_path), forge="gitlab", forge_account="gl")

    await run_forge(
        forge.FakeForge(),
        "open_mr",
        "m1",
        backend="auto",
        repo_forge="github",
        repo_entry=root,
        repositories={"lib": member},
        forges=ForgesFile(accounts={"gl": _GL}),
    )

    assert database.read(lambda c: store.forge_pins(c, "w1")) == {
        "": {"account": None, "backend": "gh"},
        "libs/core": {"account": "gl", "backend": "glab"},
    }
