"""Kraft records each push of an item's branch (`branch_pushed`), so a wait can
tell activity after the head it is waiting on from activity before it.
Walked through `forge.run_task` against a `FakeForge`; git is real."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from support.harness import commit_all, git, write

from kraft import waits
from kraft.adapters import forge
from kraft.adapters.forge import git_auth


@pytest.fixture
def on_branch(repo):
    """`repo` standing on `kraft/w1`, one commit ahead of main."""
    git(repo, "checkout", "-b", "kraft/w1")
    write(repo, "feature.txt", "one\n")
    commit_all(repo, "feature")
    return repo


def _head(repo) -> str:
    return git(repo, "rev-parse", "kraft/w1")


@pytest.mark.parametrize(
    "handler, opened_first, rebases",
    [
        ("open_mr", False, False),
        ("open_mr", True, False),
        ("sync_mr", True, False),
        ("ci_poll", True, False),
        ("ci_poll", True, True),
        ("merge", True, False),
    ],
    ids=["open_mr", "open_mr-reused", "sync_mr", "ci_poll", "rebase_conflict", "merge"],
)
async def test_a_push_is_recorded_once_per_head(
    run_forge, on_branch, monkeypatch, handler, opened_first, rebases
):
    fake = forge.FakeForge(ci_states=["success"])
    if opened_first:
        await fake.open_mr(repo=on_branch, branch="kraft/w1", base="main", title="t", body="b")
    pushed = [_head(on_branch)]
    if rebases:
        fake.mergeable, fake.merge_detail = False, "conflict"

        async def rebased(*_, **__):
            # A real rebase moves the head; the second pass finds it moved already.
            if not (on_branch / "rebased.txt").exists():
                write(on_branch, "rebased.txt", "x\n")
                commit_all(on_branch, "rebased")
            return _head(on_branch)

        monkeypatch.setattr(forge.run._builtins, "mr_rebase_forced", rebased)
        pushed.append(None)  # the head the rebase reaches, known once it ran

    await run_forge(fake, handler, "s1", repo=on_branch, orig_repo=on_branch)
    first = run_forge.events("branch_pushed")
    await run_forge(fake, handler, "s2", repo=on_branch, orig_repo=on_branch)

    if rebases:
        pushed[1] = _head(on_branch)
    assert [(p["repo"], p["sha"], p["node_id"]) for p in first] == [
        (str(on_branch), sha, "mr_checks") for sha in pushed
    ]
    assert run_forge.events("branch_pushed") == first, "a head pushed again is not a new push"
    for sha in pushed:
        assert run_forge.database.read(
            lambda c, sha=sha: waits.pushed_at(c, "w1", repo=str(on_branch), sha=sha)
        )
    assert (
        run_forge.database.read(
            lambda c: waits.pushed_at(c, "w1", repo=str(on_branch), sha="0" * 40)
        )
        is None
    )


async def test_a_head_kraft_never_pushed_is_dated_by_its_commit(on_branch):
    """The fallback for a head an older Kraft or a person pushed: no
    `branch_pushed` names it, so its committer time stands in."""
    head = _head(on_branch)
    when = await forge.git.committed_at(on_branch, head)

    assert when == datetime.fromisoformat(git(on_branch, "log", "-1", "--format=%cI", head))


async def _bump(forge_obj, root, monkeypatch, ran, *, push=None, fail=None):
    async def run_git(repo, args, *, env=None, **_):
        ran.append((args, env))
        if fail:
            raise forge.git.ForgeError(fail)
        return ""

    async def moved(*_):
        return ["m"]

    monkeypatch.setattr(forge.git, "run_git", run_git)
    monkeypatch.setattr(forge.run, "_point_at_merged_members", moved)
    rows = [{"role": "root", "repo_path": str(root)}]
    return await forge.run._bump_pointer_only_root(
        forge_obj, None, rows, branch="kraft/w1", base="main", title="t", work_item_id="w1"
    )


def _header_pinned(env) -> bool:
    return any(
        env.get(f"GIT_CONFIG_KEY_{i}", "").endswith("extraHeader")
        and env.get(f"GIT_CONFIG_VALUE_{i}", "").startswith("Authorization: Basic ")
        for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))
    )


async def test_the_member_fetch_takes_the_live_accounts_git_env(tmp_path, monkeypatch):
    sentinel = {"SENTINEL": "1"}
    ran = []

    async def auth_for(repo, *, push=False):
        return None

    async def run_git(repo, args, *, env=None, **_):
        ran.append((args, env))
        return ""

    async def default(*_, **__):
        return "main"

    async def find_mr(**_):
        return SimpleNamespace(state="merged", merged_sha="a" * 40)

    monkeypatch.setattr(git_auth, "auth_for", auth_for)
    monkeypatch.setattr(git_auth, "git_env", lambda base, auth: sentinel)
    monkeypatch.setattr(forge.git, "run_git", run_git)
    monkeypatch.setattr(forge.git, "assert_on_branch", lambda *_: None)
    monkeypatch.setattr(forge.git, "assert_no_operation", lambda *_: None)
    monkeypatch.setattr(forge.git, "assert_lfsconfig_unmoved", lambda *_: None)
    monkeypatch.setattr(forge.run._builtins, "base_branch", default)
    root = tmp_path / "root"
    db = SimpleNamespace(read=lambda fn: [{"repo_path": str(root / "m")}])
    await forge.run._point_at_merged_members(
        SimpleNamespace(find_mr=find_mr), db, root, "kraft/w1", "w1"
    )
    assert ran[0] == (["git", "fetch", "origin", "main"], sentinel)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("fatal: ... The requested URL returned error: 401", "auth", id="401"),
        pytest.param("fatal: ... The requested URL returned error: 403", "forbidden", id="403"),
    ],
)
async def test_a_member_fetch_the_token_cannot_authenticate_is_a_token_stop(
    tmp_path, monkeypatch, text, reason
):
    from .test_git_auth import auth

    async def auth_for(repo, *, push=False):
        return auth()

    async def run_git(repo, args, *, env=None, **_):
        raise forge.git.ForgeError(text)

    async def default(*_, **__):
        return "main"

    async def find_mr(**_):
        return SimpleNamespace(state="merged", merged_sha="a" * 40)

    monkeypatch.setattr(git_auth, "auth_for", auth_for)
    monkeypatch.setattr(forge.git, "run_git", run_git)
    monkeypatch.setattr(forge.git, "assert_on_branch", lambda *_: None)
    monkeypatch.setattr(forge.run._builtins, "base_branch", default)
    db = SimpleNamespace(read=lambda fn: [{"repo_path": str(tmp_path / "m")}])
    with pytest.raises(forge.git.ForgeError) as caught:
        await forge.run._point_at_merged_members(
            SimpleNamespace(find_mr=find_mr), db, tmp_path, "kraft/w1", "w1"
        )
    assert (caught.value.reason, caught.value.account) == (reason, "corp-gl")


async def test_a_cli_backends_pointer_bump_push_carries_no_header(tmp_path, monkeypatch):
    ran = []

    async def boom(*_, **__):
        raise AssertionError("a CLI backend looked up an account")

    monkeypatch.setattr(git_auth, "auth_for", boom)
    monkeypatch.setattr(git_auth, "auth_for_account", boom)
    await _bump(SimpleNamespace(), tmp_path, monkeypatch, ran)
    assert ran[0][0] == ["git", "push", "--no-verify", "origin", "HEAD:main"]
    assert not _header_pinned(ran[0][1])


async def test_a_pointer_bump_pushes_with_the_pinned_account_not_the_live_one(
    tmp_path, monkeypatch
):
    """With no live reader at all the push still carries `corp-gl`'s header, from the pin; and
    once a reader that would name another account is installed, it is never asked."""
    from kraft.adapters.forge.gitlab_api import GitLabApi

    from .test_git_auth import HTTPS, acct, form, rig

    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, reader=False)
    ran = []
    pinned = GitLabApi("corp-gl", acct())
    await _bump(pinned, repo, monkeypatch, ran)
    args, env = ran[0]
    assert args == ["git", "push", "--no-verify", "origin", "HEAD:main"]
    assert f"Authorization: Basic {form()}" in env.values()

    async def other(repo, *, push=False):
        raise AssertionError("the live account was asked")

    monkeypatch.setattr(git_auth, "auth_for", other)
    ran.clear()
    await _bump(pinned, repo, monkeypatch, ran)
    assert f"Authorization: Basic {form()}" in ran[0][1].values()


@pytest.mark.parametrize(
    ("setup", "words"),
    [
        pytest.param({"token": False}, "set it there and restart", id="unreadable-token"),
        pytest.param(
            {"config_lines": [("remote.origin.pushurl", "https://other.example/t.git")]},
            "will not send its token",
            id="push-url-elsewhere",
        ),
    ],
)
async def test_a_pointer_bump_push_the_pin_refuses_stops_and_never_opens_a_merge_request(
    tmp_path, monkeypatch, setup, words
):
    from kraft.adapters.forge.gitlab_api import GitLabApi

    from .test_git_auth import HTTPS, acct, rig

    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, reader=False, **setup)
    opened = []

    class Pinned(GitLabApi):
        async def push(self, **_):
            opened.append("push")

        async def open_mr(self, **_):
            opened.append("mr")

    ran = []
    with pytest.raises(forge.git.ForgeError, match=words):
        await _bump(Pinned("corp-gl", acct()), repo, monkeypatch, ran)
    assert ran == [] and opened == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("... The requested URL returned error: 401", "auth", id="401"),
        pytest.param("... The requested URL returned error: 403", "forbidden", id="403"),
    ],
)
async def test_a_refused_token_on_the_direct_pointer_push_stops_with_one_attempt(
    tmp_path, monkeypatch, text, reason
):
    from kraft.adapters.forge.gitlab_api import GitLabApi

    from .test_git_auth import HTTPS, acct, rig

    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, reader=False)
    pushed = []

    class Pinned(GitLabApi):
        async def push(self, **_):
            pushed.append("second push")

    ran = []
    with pytest.raises(forge.git.ForgeError) as caught:
        await _bump(Pinned("corp-gl", acct()), repo, monkeypatch, ran, fail=text)
    assert (caught.value.reason, caught.value.account) == (reason, "corp-gl")
    assert len(ran) == 1 and pushed == []
