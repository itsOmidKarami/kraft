"""`run_task` pins each repo's forge account and backend at the item's first
forge node, and stops the item when a pin no longer holds."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from support.harness import git, make_repo, own_files

from kraft import apply, store
from kraft.adapters import forge
from kraft.config import RepoEntry
from kraft.forges import ForgeAccount, ForgesFile

from .nodes import back_half

_GH = ForgeAccount(forge="github", web_url="https://github.com")
_GL = ForgeAccount(forge="gitlab", web_url="https://gitlab.example")
_GH_DEFAULT = ForgeAccount(forge="github", web_url="https://github.com", default_for_host=True)
_GH_API = ForgeAccount(
    forge="github", web_url="https://github.com", client="api", token={"from": "git-credential"}
)
_GH_API_DEFAULT = ForgeAccount(**{**_GH_API.model_dump(), "default_for_host": True})
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


class _Tagged(forge.FakeForge):
    """A `FakeForge` that says which backend ran the node."""

    def __init__(self, tag, ran):
        super().__init__()
        self.tag, self.ran = tag, ran

    async def open_mr(self, **kwargs):
        self.ran.append(self.tag)
        return await super().open_mr(**kwargs)


@pytest.fixture
def backends(monkeypatch):
    """`api_forge` and `resolve` as recorders: what each was asked for, and which one ran."""
    seen = SimpleNamespace(api=[], resolved=[], ran=[])

    def api(name, account, **kw):
        seen.api.append((name, account))
        return _Tagged("api", seen.ran)

    monkeypatch.setattr(forge.run, "api_forge", api)
    return seen


_HOSTS = {"github": "github.com", "gitlab": "gitlab.example"}


@pytest.mark.parametrize(
    "root, accounts, named, member, member_account, pins, ran",
    [
        pytest.param(
            "gitlab",
            {"corp-gl": _GL_API},
            "corp-gl",
            None,
            None,
            {"": {"account": "corp-gl", "backend": "gitlab-api"}},
            ["api"],
            id="gitlab-named-account",
        ),
        pytest.param(
            "gitlab",
            {"corp-gl": _GL_API},
            None,
            None,
            None,
            {"": {"account": "corp-gl", "backend": "gitlab-api"}},
            ["api"],
            id="gitlab-host-default",
        ),
        pytest.param(
            "gitlab",
            {"corp-gl": _GL_API},
            None,
            "gitlab",
            "corp-gl",
            {
                "": {"account": None, "backend": "gh"},
                "libs/core": {"account": "corp-gl", "backend": "gitlab-api"},
            },
            ["cli"],
            id="gitlab-member-on-an-api-account",
        ),
        pytest.param(
            "gitlab",
            {"corp-gl": _GL_API},
            None,
            "",
            None,
            {
                "": {"account": "corp-gl", "backend": "gitlab-api"},
                "libs/core": {"account": "corp-gl", "backend": "gitlab-api"},
            },
            ["api"],
            id="gitlab-member-with-no-forge-takes-the-roots-api-pin",
        ),
        pytest.param(
            "github",
            {"corp-gh": _GH_API},
            "corp-gh",
            None,
            None,
            {"": {"account": "corp-gh", "backend": "github-api"}},
            ["api"],
            id="github-named-account",
        ),
        pytest.param(
            "github",
            {"corp-gh": _GH_API_DEFAULT},
            None,
            None,
            None,
            {"": {"account": "corp-gh", "backend": "github-api"}},
            ["api"],
            id="github-host-default",
        ),
        pytest.param(
            "github",
            {"corp-gh": _GH_API},
            None,
            "github",
            "corp-gh",
            {
                "": {"account": None, "backend": "glab"},
                "libs/core": {"account": "corp-gh", "backend": "github-api"},
            },
            ["cli"],
            id="github-member-on-an-api-account",
        ),
        pytest.param(
            "github",
            {"corp-gh": _GH_API},
            "corp-gh",
            "",
            None,
            {
                "": {"account": "corp-gh", "backend": "github-api"},
                "libs/core": {"account": "corp-gh", "backend": "github-api"},
            },
            ["api"],
            id="github-member-with-no-forge-takes-the-roots-api-pin",
        ),
    ],
)
async def test_an_api_account_runs_through_the_api_and_never_the_cli(
    run_forge,
    database,
    tmp_path,
    monkeypatch,
    backends,
    root,
    accounts,
    named,
    member,
    member_account,
    pins,
    ran,
):
    path = make_repo(tmp_path)
    git(path, "remote", "add", "origin", f"https://{_HOSTS[root]}/o/r.git")
    forge_name = {"github": "gitlab", "gitlab": "github"}[root] if member else root
    if member is not None:
        monkeypatch.setattr(forge.run._builtins, "item_members", lambda row: [("libs/core", "lib")])
    cli = _Tagged("cli", backends.ran)
    result = await run_forge(
        cli,
        "open_mr",
        "a1",
        repo=path,
        backend="auto",
        repo_forge=forge_name,
        repo_entry=RepoEntry(path=str(path), forge=forge_name, forge_account=named),
        repositories=(
            {"lib": RepoEntry(path=str(path), forge=member or None, forge_account=member_account)}
            if member is not None
            else {}
        ),
        forges=ForgesFile(accounts=accounts),
    )

    assert result == ("done", "done"), run_forge.log("a1")
    assert database.read(lambda c: store.forge_pins(c, "w1")) == pins
    assert backends.ran == ran
    # Each API account is built once, whoever it serves.
    assert backends.api == [(next(iter(accounts)), next(iter(accounts.values())))]


_API = ("api", "corp-gl")


@pytest.mark.parametrize(
    "pins, live, members",
    [
        pytest.param(
            {
                "": {"account": "corp-gl", "backend": "gitlab-api"},
                "libs/core": {"account": "gl-cli", "backend": "glab"},
            },
            _API,
            {"libs/core": ("cli", "glab")},
            id="cli-member-under-api-root-keeps-glab",
        ),
        pytest.param(
            {
                "": {"account": "corp-gl", "backend": "gitlab-api"},
                "libs/core": {"account": "corp-gl", "backend": "gitlab-api"},
            },
            _API,
            {"libs/core": _API},
            id="members-on-the-roots-account-share-its-instance",
        ),
        pytest.param(
            {
                "": {"account": None, "backend": "gh"},
                "libs/core": {"account": "corp-gl", "backend": "gitlab-api"},
            },
            ("cli", "gh"),
            {"libs/core": _API},
            id="api-member-under-a-cli-root",
        ),
        pytest.param(
            {
                "": {"account": "corp-gh", "backend": "github-api"},
                "libs/core": {"account": None, "backend": "gh"},
            },
            ("api", "corp-gh"),
            {"libs/core": ("cli", "gh")},
            id="gh-member-under-a-github-api-root",
        ),
    ],
)
def test_every_mount_is_run_from_its_own_pin(monkeypatch, pins, live, members):
    built = []
    monkeypatch.setattr(forge.run, "api_forge", lambda n, a, **kw: built.append(n) or ("api", n))
    monkeypatch.setattr(forge.run, "resolve", lambda name: ("cli", name))
    monkeypatch.setattr(forge.run, "_member_forges", lambda *a: {})
    forges = ForgesFile(accounts={"corp-gl": _GL_API, "gl-cli": _GL, "corp-gh": _GH_API})

    got = forge.run._mount_forges(pins, None, "auto", "github", forges, None)

    assert got == (live, members)
    # one instance per account
    assert sorted(built) == sorted(
        {p["account"] for p in pins.values() if p["backend"][-3:] == "api"}
    )


@pytest.mark.parametrize(
    "pins, forges, error, mount, built, stop",
    [
        pytest.param(
            {"": ("corp-gl", "gitlab-api")},
            {"corp-gl": _GL_API},
            None,
            None,
            ["corp-gl"],
            None,
            id="root-pinned",
        ),
        pytest.param(
            {}, {"corp-gl": _GL_API}, None, None, ["corp-gl"], None, id="root-live-before-any-pin"
        ),
        pytest.param(
            {"": (None, "gh"), "libs/core": ("corp-gl", "gitlab-api")},
            {"corp-gl": _GL_API},
            None,
            "libs/core",
            ["corp-gl"],
            None,
            id="member-pinned-to-its-own-account",
        ),
        pytest.param(
            {"": ("corp-gl", "gitlab-api")},
            {"corp-gl": _GL_API},
            None,
            "libs/core",
            ["corp-gl"],
            None,
            id="member-without-forge-takes-the-roots",
        ),
        pytest.param(
            {"": ("corp-gl", "gitlab-api")},
            {},
            None,
            None,
            [],
            "forge account corp-gl was removed",
            id="pinned-account-removed-stops",
        ),
        pytest.param(
            {"": ("corp-gl", "gitlab-api")},
            None,
            "bad yaml",
            None,
            [],
            "forges.yaml does not load (bad yaml)",
            id="pinned-while-forges-yaml-does-not-load-stops",
        ),
        pytest.param(
            {"": ("corp-gh", "github-api")},
            {"corp-gh": _GH_API},
            None,
            None,
            ["corp-gh"],
            None,
            id="github-api-root-pinned",
        ),
        pytest.param(
            {"": (None, "glab")},
            {"corp-gl": _GL_API},
            None,
            None,
            [],
            None,
            id="cli-pin-stays-cli",
        ),
    ],
)
async def test_item_forge_builds_the_api_backend_from_the_pin(
    item_on, database, tmp_path, monkeypatch, pins, forges, error, mount, built, stop
):
    path = make_repo(tmp_path)
    git(path, "remote", "add", "origin", "https://gitlab.example/o/r.git")
    await item_on(back_half(), repo=path)
    for at, (account, backend) in pins.items():
        await database.write(
            lambda c, a=at, x=account, b=backend: store.set_forge_pin(c, "w1", a, x, b)
        )
    calls = []
    monkeypatch.setattr(forge.run, "api_forge", lambda n, a, **kw: calls.append(n) or ("api", n))
    monkeypatch.setattr(forge.run, "resolve", lambda name: ("cli", name))
    monkeypatch.setattr(forge.run._builtins, "item_members", lambda row: [("libs/core", "lib")])
    monkeypatch.setattr(
        forge.run,
        "load_repos",
        lambda p: [
            RepoEntry(
                id="lib", path=str(path), forge="gitlab" if mount and pins.get(mount) else None
            )
        ],
    )
    entry = RepoEntry(path=str(path), forge="gitlab")
    row = {"id": "w1"}
    args = (ForgesFile(accounts=forges) if forges is not None else None, error)

    async def build():
        return await forge.run.item_forge(database, row, entry, *args, mount=mount)

    if stop:
        with pytest.raises(forge.ForgeError, match=stop.replace("(", r"\(").replace(")", r"\)")):
            await build()
    else:
        got = await build()
        assert got == (("api", built[0]) if built else ("cli", "glab"))
    assert calls == built


@pytest.mark.parametrize(
    "error, record",
    [
        pytest.param(
            {"reason": "auth", "account": "corp-gl", "source": "env CORP_GL"},
            {"account": "corp-gl", "source": "env CORP_GL"},
            id="api-account-named",
        ),
        pytest.param({"reason": "auth"}, {}, id="cli-error-names-no-account"),
    ],
)
async def test_a_forge_error_record_carries_the_account_and_source_it_names(
    run_forge, run_dirs, repo, error, record
):
    class Refused(forge.FakeForge):
        async def open_mr(self, **kwargs):
            raise forge.ForgeError("HTTP 401", status=401, **error)

    await _run(run_forge, repo, "r1", {}, None)  # the item and its pin
    await run_forge(Refused(), "open_mr", "r2", repo=repo, backend="gh")

    saved = json.loads(own_files(run_dirs, "r2").log.with_suffix(".forge.json").read_text())
    assert {k: v for k, v in saved.items() if k in ("account", "source")} == record
    assert saved["reason"] == "auth"
