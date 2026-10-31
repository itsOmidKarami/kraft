"""`run_task` pins each repo's forge account and backend at the item's first
forge node, and stops the item when a pin no longer holds."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from support.harness import git, make_repo

from kraft import apply, store
from kraft.adapters import forge
from kraft.config import RepoEntry
from kraft.forges import ForgeAccount, ForgesFile

_GH = ForgeAccount(forge="github", web_url="https://github.com")
_GL = ForgeAccount(forge="gitlab", web_url="https://gitlab.example")
_GH_DEFAULT = ForgeAccount(forge="github", web_url="https://github.com", default_for_host=True)
_GH_API = ForgeAccount(
    forge="github", web_url="https://github.com", client="api", token={"from": "git-credential"}
)
_GL_API = ForgeAccount(
    forge="gitlab",
    web_url="https://gitlab.example",
    client="api",
    default_for_host=True,
    token={"from": "git-credential"},
)


@pytest.fixture
def repo(tmp_path):
    """A real checkout whose origin is on github.com, so the origin host is known."""
    path = make_repo(tmp_path)
    git(path, "remote", "add", "origin", "https://github.com/o/r.git")
    return path


def _run(run_forge, repo, sid, accounts, named, forge_name="github", error=None):
    """One `open_mr` node; `accounts` None is a forges.yaml that never loaded."""
    return run_forge(
        forge.FakeForge(),
        "open_mr",
        sid,
        repo=repo,
        backend="auto",
        repo_forge=forge_name,
        repo_entry=RepoEntry(path=str(repo), forge=forge_name, forge_account=named),
        repositories={},
        forges=None if accounts is None else ForgesFile(accounts=accounts),
        forges_error=error,
    )


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
            # the origin's default account would now apply, but a null pin never re-pins
            ({"a": _GH_DEFAULT}, None),
            None,
            {"": {"account": None, "backend": "gh"}},
            id="null-pin-never-stops",
        ),
        pytest.param(
            ({}, None),
            ({}, "ghost"),
            None,
            {"": {"account": None, "backend": "gh"}},
            id="null-pin-ignores-a-later-unknown-account",
        ),
        pytest.param(
            ({"a": _GL}, "a"),
            None,
            "forge_account a says forge: gitlab, the repo says forge: github",
            {},
            id="named-account-on-wrong-forge-stops",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            ({"a": _GH_API}, "a"),
            "forge account a now says client: api, was client: cli",
            {"": {"account": "a", "backend": "gh"}},
            id="client-changed-to-api-stops",
        ),
        pytest.param(
            ({"a": _GH_API}, "a"),
            ({"a": _GH}, "a"),
            "forge account a now says client: cli, was client: api",
            {"": {"account": "a", "backend": "github-api"}},
            id="client-changed-to-cli-stops",
        ),
        pytest.param(
            ({}, None),
            # a null pin is a CLI pin: the account that now applies never re-pins it
            ({"a": ForgeAccount(**{**_GH_API.model_dump(), "default_for_host": True})}, None),
            None,
            {"": {"account": None, "backend": "gh"}},
            id="null-pin-keeps-the-cli",
        ),
        pytest.param(
            ({"a": _GH}, "a"),
            ({"a": _GH}, "a", "gitlab"),
            "forge account a is pinned to gh, the repo now says forge: gitlab",
            {"": {"account": "a", "backend": "gh"}},
            id="repo-forge-changed-stops",
        ),
    ],
)
async def test_the_first_forge_node_pins_and_a_broken_pin_stops_the_item(
    run_forge, database, repo, first, second, stop, pins_after
):
    result = await _run(run_forge, repo, "p1", *first)
    if second is not None:
        result = await _run(run_forge, repo, "p2", *second)
    sid = "p2" if second is not None else "p1"

    if stop is None:
        assert result == ("done", "done")
    else:
        assert result == ("config_error", "config_error")
        assert stop in run_forge.log(sid)
        if "forge_account" not in stop:
            assert "a person's" in run_forge.log(sid) and "kraft item retry" in run_forge.log(sid)
    assert database.read(lambda c: store.forge_pins(c, "w1")) == pins_after


@pytest.mark.parametrize(
    "first, second, stop, pins_after",
    [
        pytest.param(
            ({"a": _GH}, "a", None),
            "refused reload",
            None,
            {"": {"account": "a", "backend": "gh"}},
            id="refused-reload-keeps-pin",
        ),
        pytest.param(
            ({"a": _GH}, "a", None),
            (None, "a", "bad yaml"),
            None,
            {"": {"account": "a", "backend": "gh"}},
            id="restart-unreadable-keeps-pin",
        ),
        pytest.param(
            (None, None, "bad yaml"),
            None,
            "forges.yaml does not load (bad yaml); fix it, then a person's kraft item retry",
            {},
            id="unreadable-file-stops-unpinned-host-default",
        ),
        pytest.param(
            (None, "a", "bad yaml"),
            None,
            "forge_account a: forges.yaml does not load (bad yaml)",
            {},
            id="restart-unreadable-named-stops",
        ),
    ],
)
async def test_a_forges_yaml_that_does_not_load_never_unpins_or_stops_a_pinned_item(
    run_forge, database, repo, tmp_path, first, second, stop, pins_after
):
    accounts, named, error = first
    result = await _run(run_forge, repo, "p1", accounts, named, error=error)
    sid = "p1"
    if second == "refused reload":
        # The running config is kept, so the next run receives the same accounts.
        config = tmp_path / "config"
        config.mkdir()
        two_defaults = {
            "forge": "github",
            "web_url": "https://github.com",
            "default_for_host": True,
        }
        (config / "forges.yaml").write_text(
            yaml.safe_dump({"accounts": {"a": two_defaults, "b": two_defaults}})
        )
        st = SimpleNamespace(
            templates_dir=config, forges=ForgesFile(accounts=first[0]), invalid_forges=None
        )
        before = st.forges
        apply._reload_forges(st)
        assert st.invalid_forges and st.forges is before
        sid = "p2"
        result = await _run(run_forge, repo, sid, st.forges.accounts, "a")
    elif second is not None:
        accounts, named, error = second
        sid = "p2"
        result = await _run(run_forge, repo, sid, accounts, named, error=error)

    if stop is None:
        assert result == ("done", "done")
    else:
        assert result == ("config_error", "config_error")
        assert stop in run_forge.log(sid)
    assert database.read(lambda c: store.forge_pins(c, "w1")) == pins_after


async def test_a_repo_with_no_accounts_and_none_named_never_reads_its_origin(
    run_forge, database, repo, monkeypatch
):
    def refuse(*args, **kwargs):
        raise AssertionError("the origin was read with nothing to match it against")

    monkeypatch.setattr(forge.run.config, "git_read", refuse)
    assert await _run(run_forge, repo, "p1", {}, None) == ("done", "done")
    assert database.read(lambda c: store.forge_pins(c, "w1")) == {
        "": {"account": None, "backend": "gh"}
    }


async def test_a_member_is_pinned_by_its_mount(run_forge, database, tmp_path, monkeypatch):
    monkeypatch.setattr(forge.run._builtins, "item_members", lambda row: [("libs/core", "lib")])
    root = RepoEntry(path=str(tmp_path), forge="github")
    member = RepoEntry(path=str(tmp_path), forge="gitlab", forge_account="gl")
    writes = []
    write = database.write

    async def spy(fn):
        writes.append(fn.__qualname__)
        return await write(fn)

    monkeypatch.setattr(database, "write", spy)

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
    # root and member are pinned by one write, not one per mount
    assert [w for w in writes if w.startswith("_pin_forge_accounts")] == [
        "_pin_forge_accounts.<locals>.write"
    ]


class _NeverRun:
    def __getattr__(self, name):
        raise AssertionError(f"the CLI forge ran: {name}")


@pytest.mark.parametrize(
    "accounts, named, member, pins",
    [
        pytest.param(
            {"corp-gl": _GL_API},
            "corp-gl",
            None,
            {"": {"account": "corp-gl", "backend": "gitlab-api"}},
            id="named-account",
        ),
        pytest.param(
            {"corp-gl": _GL_API},
            None,
            None,
            {"": {"account": "corp-gl", "backend": "gitlab-api"}},
            id="host-default",
        ),
        pytest.param(
            {"corp-gl": _GL_API},
            None,
            True,
            {
                "": {"account": None, "backend": "gh"},
                "libs/core": {"account": "corp-gl", "backend": "gitlab-api"},
            },
            id="member-on-an-api-account",
        ),
    ],
)
async def test_an_api_account_stops_at_the_first_forge_node_and_never_runs_the_cli(
    run_forge, database, tmp_path, monkeypatch, accounts, named, member, pins
):
    path = make_repo(tmp_path)
    git(path, "remote", "add", "origin", "https://gitlab.example/o/r.git")
    forge_name = "github" if member else "gitlab"
    if member:
        monkeypatch.setattr(forge.run._builtins, "item_members", lambda row: [("libs/core", "lib")])
    result = await run_forge(
        _NeverRun(),
        "open_mr",
        "a1",
        repo=path,
        backend="auto",
        repo_forge=forge_name,
        repo_entry=RepoEntry(path=str(path), forge=forge_name, forge_account=named),
        repositories=(
            {"lib": RepoEntry(path=str(path), forge="gitlab", forge_account="corp-gl")}
            if member
            else {}
        ),
        forges=ForgesFile(accounts=accounts),
    )

    assert result == ("config_error", "config_error")
    assert (
        "forge account corp-gl says client: api, and the gitlab-api backend is not in this release"
        in run_forge.log("a1")
    )
    assert database.read(lambda c: store.forge_pins(c, "w1")) == pins
