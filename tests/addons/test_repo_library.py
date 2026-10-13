"""A connected repository's `.kraft/` folder, read from origin's default branch."""

import json
import subprocess

import pytest
import yaml
from support.addons import (
    AGENT,
    chain,
    edit_store,
    install,
    instance,
    make_collection,
    repo_library_files,
    with_origin,
)
from support.harness import commit_all, git, make_repo, write

from kraft.addons import fetch, load, manifest, repo_library, update
from kraft.addons.config import RepoLibrariesLock
from kraft.config import read_yaml, write_yaml

_library = repo_library_files


@pytest.fixture
def app(tmp_path):
    repo = make_repo(tmp_path, "app")
    with_origin(repo)
    return repo


def test_the_commit_comes_from_the_remote_not_the_checkout(app):
    pushed = _library(app)
    # A commit the remote never saw, and a remote-tracking ref moved onto it.
    write(app, ".kraft/library.yaml", yaml.safe_dump({"tasks": {"base": {**AGENT, "prompt": "x"}}}))
    local = commit_all(app, "not pushed")
    branch = git(app, "rev-parse", "--abbrev-ref", "HEAD")
    git(app, "update-ref", f"refs/remotes/origin/{branch}", local)

    found = repo_library.remote(app)

    assert (found.commit, found.branch) == (pushed, branch)
    assert "@" not in found.url


def test_extract_gives_the_layout_files_and_a_derived_manifest(app):
    head = _library(
        app,
        library={"tasks": {"base": {**AGENT, "profile": "strong"}}},
        skills={"notes": "# notes\n"},
        extra={".kraft/collection.json": "{}", ".kraft/README.md": "hi"},
    )

    extracted = repo_library.extract(app, head)

    assert sorted(extracted.files) == [
        ".kraft/addon.json",
        "chains/ship.yaml",
        "library.yaml",
        "skills/notes/SKILL.md",
    ]
    declared = json.loads(extracted.files[manifest.ADDON_JSON][1])
    assert declared["name"] == "library"
    assert declared["requires"]["harnesses"] == ["codex"]
    assert declared["requires"]["profiles"] == ["strong"]
    assert manifest.addon(declared, manifest.ADDON_JSON).name == "library"
    assert extracted.tree == git(app, "rev-parse", f"{head}:.kraft")


def test_the_same_files_give_the_same_digest(app):
    first = repo_library.extract(app, _library(app))
    write(app, "README.md", "unrelated\n")
    commit_all(app, "elsewhere")
    git(app, "push", "-q", "origin", "HEAD")

    again = repo_library.extract(app, repo_library.remote(app).commit)

    assert fetch.digest(again.files) == fetch.digest(first.files)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no-kraft-folder"),
        pytest.param({".kraft/collection.json": "{}"}, id="only-a-collection"),
    ],
)
def test_a_repo_with_no_layout_file_has_no_repo_library(app, files):
    for rel, text in files.items():
        write(app, rel, text)
    write(app, "x", "x")
    head = commit_all(app, "no library")
    git(app, "push", "-q", "origin", "HEAD")

    assert repo_library.extract(app, head) is None


def test_a_commit_the_checkout_lacks_is_fetched(app, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", git(app, "remote", "get-url", "origin"), str(other))
    head = _library(app)
    lacks = subprocess.run(["git", "-C", str(other), "cat-file", "-e", head], check=False)
    assert lacks.returncode != 0

    assert repo_library.extract(other, head) is not None


def test_a_repo_with_no_origin_is_refused_by_name(tmp_path):
    repo = make_repo(tmp_path, "lonely")

    with pytest.raises(fetch.AddonRefused, match="no remote named origin"):
        repo_library.remote(repo)


def test_an_origin_that_does_not_answer_fails_as_network(app):
    git(app, "remote", "set-url", "origin", (app.parent / "gone.git").as_uri())

    with pytest.raises(fetch.FetchError) as caught:
        repo_library.remote(app)
    assert caught.value.kind == "network"


def test_missing_blobs_are_refused_with_the_commit_named(app, monkeypatch):
    head = _library(app)

    def gone(*_a, **_k):
        raise IndexError("missing")

    monkeypatch.setattr(fetch, "_sizes", gone)

    with pytest.raises(fetch.AddonRefused, match=head[:12]):
        repo_library.extract(app, head)


@pytest.fixture
def home(tmp_path, app):
    """An instance with `app` connected under the id `app`, nothing accepted."""
    found = instance(tmp_path)
    write_yaml(found[0] / "repos.yaml", {"repos": [{"path": str(app), "id": "app"}]})
    return found


def test_a_first_load_waits_with_everything_shown_as_new(app, home):
    head = _library(app, skills={"notes": "# notes\n"})

    found = repo_library.check(app, "app", *home)

    assert found.outcome == "waiting"
    assert found.review.old_version is None
    assert any("skill notes" in line for line in found.review.content)
    assert any(head[:12] in line for line in found.review.content)
    assert load.installed(*home) == ()


def test_accept_writes_the_store_and_the_lock(app, home):
    head = _library(app)
    found = repo_library.check(app, "app", *home)

    repo_library.accept(found, *home)

    locked = RepoLibrariesLock.load(home[0] / "repo-libraries.lock").repos["app"]
    assert (locked.commit, locked.digest) == (head, found.digest)
    (library,) = load.installed(*home, verify=True)
    assert (library.namespace, library.left_out) == ("app", None)
    assert repo_library.check(app, "app", *home).outcome == "current"


@pytest.mark.parametrize("edited", [False, True], ids=["unchanged", "store-edited"])
def test_a_change_is_reviewed_against_the_accepted_version(app, home, edited):
    """A store edited since it was accepted is never what the change is
    diffed against: the review says it could not compare."""
    _library(app)
    accepted = repo_library.check(app, "app", *home)
    repo_library.accept(accepted, *home)
    if edited:
        store = home[1] / "store" / accepted.digest.removeprefix("sha256:")
        edit_store(store, "library.yaml", "tasks: {base: {kind: agent, prompt: new words}}\n")
    _library(app, library={"tasks": {"base": {**AGENT, "prompt": "new words"}}})

    found = repo_library.check(app, "app", *home)

    assert found.outcome == "waiting"
    if edited:
        unread = "the accepted version's files could not be read to compare with"
        assert found.review.reach[0] == unread
        return
    assert found.review.old_version is not None
    assert found.review.old_version != found.review.new_version
    assert any("base" in line for line in (*found.review.reach, *found.review.content))


def test_a_remote_with_no_library_is_none_not_a_fault(app, home):
    assert repo_library.check(app, "app", *home).outcome == "none"


@pytest.mark.parametrize(
    "library, chains, why",
    [
        pytest.param(
            {"tasks": {"base": {"kind": "subprocess", "command": "true"}}},
            None,
            "subprocess",
            id="subprocess",
        ),
        pytest.param(
            {"tasks": {"base": {**AGENT, "policy": {"grants": ["git-push"]}}}},
            None,
            "policy.grants is not a limit",
            id="permission",
        ),
        pytest.param(
            {"tasks": {"base": {**AGENT, "harness": "nope"}}},
            None,
            "requires harness 'nope'",
            id="harness-the-instance-lacks",
        ),
        pytest.param(
            {"tasks": {"base": {**AGENT, "extends": "app:other"}}},
            None,
            "reaches outside",
            id="its-own-repo-id-as-a-qualifier",
        ),
        pytest.param(None, {"ship": chain("missing")}, "does not resolve", id="broken-chain"),
    ],
)
def test_a_repo_library_is_refused_for_what_an_addon_is(app, home, library, chains, why):
    _library(app, library=library, chains=chains)

    found = repo_library.check(app, "app", *home)

    assert found.outcome == "refused"
    assert any(why in problem for problem in found.problems), found.problems


@pytest.mark.parametrize(
    "repo_id, why",
    [
        pytest.param("kraft", "Kraft's own namespace", id="reserved"),
        pytest.param("release", "add-on release@acme", id="an-addons-namespace"),
        pytest.param("a" * 65, "longer than 64", id="too-long"),
    ],
)
def test_an_id_that_cannot_be_a_namespace_is_refused(tmp_path, app, home, repo_id, why):
    collection = make_collection(tmp_path, {"release": {"library": {"tasks": {"base": AGENT}}}})
    install(*home, collection, "release")
    _library(app)
    repos = {"repos": [{"path": str(app), "id": repo_id}]}

    found = repo_library.check(app, repo_id, *home, repos=repos)

    assert found.outcome == "refused"
    assert why in found.problems[0]
    assert "set another `id`" in found.problems[0]


def test_a_local_chain_through_the_library_is_in_the_review(app, home):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    write(home[0], "chains/mine.yaml", yaml.safe_dump(chain("app:base")))
    _library(app, library={"tasks": {"base": {**AGENT, "policy": {"budget_usd": 5}}}})

    found = repo_library.check(app, "app", *home)

    assert any(line.startswith("mine.") for line in found.review.reach), found.review.reach


def test_an_accept_after_the_lock_moved_writes_nothing(app, home):
    _library(app)
    stale = repo_library.check(app, "app", *home)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    before = (home[0] / "repo-libraries.lock").read_bytes()

    with pytest.raises(update.Busy, match="changed while you reviewed"):
        repo_library.accept(stale, *home)

    assert (home[0] / "repo-libraries.lock").read_bytes() == before


def test_a_remote_that_does_not_answer_is_failed_and_keeps_the_lock(app, home):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    git(app, "remote", "set-url", "origin", (app.parent / "gone.git").as_uri())

    found = repo_library.check(app, "app", *home)

    assert (found.outcome, found.kind) == ("failed", "network")
    assert load.installed(*home)[0].left_out is None


def test_drop_forgets_the_accepted_version(app, home):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)

    assert repo_library.drop("app", *home) is True
    assert repo_library.drop("app", *home) is False
    assert load.installed(*home) == ()


@pytest.mark.parametrize(
    "default_chain, said",
    [
        pytest.param(None, True, id="unset"),
        pytest.param("default", True, id="default"),
        pytest.param("quick", False, id="the-entry-names-another"),
    ],
)
def test_a_first_load_says_when_it_becomes_the_repos_default(app, home, default_chain, said):
    _library(app, chains={"default": chain("base")})

    found = repo_library.check(app, "app", *home, default_chain=default_chain)

    line = "becomes the chain new items in this repo run: app:default"
    assert (line in found.review.reach) is said


def _moved_remote(app):
    elsewhere = app.parent / "elsewhere.git"
    origin = git(app, "remote", "get-url", "origin")
    git(app.parent, "clone", "-q", "--bare", origin, str(elsewhere))
    git(app, "remote", "set-url", "origin", elsewhere.as_uri())
    _library(app, library={"tasks": {"base": {**AGENT, "prompt": "moved"}}})
    return "origin's URL changed: "


def _renamed_branch(app):
    git(app, "checkout", "-q", "-b", "trunk")
    _library(app, library={"tasks": {"base": {**AGENT, "prompt": "trunk"}}})
    git(app.parent / f"{app.name}-origin.git", "symbolic-ref", "HEAD", "refs/heads/trunk")
    return "the default branch changed: "


@pytest.mark.parametrize(
    "then",
    [
        pytest.param(_moved_remote, id="another-url"),
        pytest.param(_renamed_branch, id="another-default-branch"),
    ],
)
def test_a_change_from_somewhere_else_is_said_as_reach(app, home, then):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)

    line = then(app)

    found = repo_library.check(app, "app", *home)
    assert found.outcome == "waiting"
    assert any(entry.startswith(line) for entry in found.review.reach), found.review.reach


@pytest.mark.parametrize(
    "first, then, default_chain, line",
    [
        pytest.param(
            {"ship": chain("base")},
            {"ship": chain("base"), "default": chain("base")},
            None,
            "becomes the chain new items in this repo run: app:default",
            id="gains-default",
        ),
        pytest.param(
            {"ship": chain("base"), "default": chain("base")},
            {"ship": chain("base")},
            "default",
            "new items in this repo go back to the instance's default chain",
            id="loses-default",
        ),
        pytest.param(
            {"ship": chain("base")},
            {"ship": chain("base"), "default": chain("base")},
            "quick",
            None,
            id="the-entry-names-another",
        ),
    ],
)
def test_a_change_that_moves_the_repos_default_chain_is_reach(
    app, home, first, then, default_chain, line
):
    _library(app, chains=first)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    if "default" not in then:
        git(app, "rm", "-q", ".kraft/chains/default.yaml")
    _library(app, chains=then, library={"tasks": {"base": {**AGENT, "prompt": "v2"}}})

    found = repo_library.check(app, "app", *home, default_chain=default_chain)

    about_default = [e for e in found.review.reach if "default chain" in e or "app:default" in e]
    assert about_default == ([line] if line else [])


_GATED = {
    "nodes": [
        {"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base", "skill": "notes"}]},
        {
            "id": "ok",
            "kind": "gate",
            "auto_review": {**AGENT, "id": "reviewer", "skill": "verdict", "steering": ["house"]},
        },
    ]
}
_BASE = {"tasks": {"base": AGENT}, "steering": {"house": {"instructions": "be kind"}}}


def _judged(words):
    """A one-node chain whose fix loop's judge is told `words`."""
    loop = {
        "tasks": [{"id": "repair", "extends": "base"}],
        "judge": {**AGENT, "id": "judge", "prompt": words},
    }
    task = {"id": "t", "extends": "base"}
    return {"nodes": [{"id": "fix", "kind": "exec", "tasks": [task], "fix_loop": loop}]}


_JUDGED = {"ship": _GATED, "fixed": _judged("judge well")}
_SKILLS = {"notes": "# notes\n", "verdict": "# verdict\n"}


def _v1(app):
    return _library(app, library=_BASE, chains=_JUDGED, skills=_SKILLS)


@pytest.mark.parametrize(
    "change, holds",
    [
        pytest.param(
            {"library": {**_BASE, "tasks": {"base": {**AGENT, "prompt": "new words"}}}},
            [],
            id="a-prompt",
        ),
        pytest.param(
            {"skills": {**_SKILLS, "notes": "# notes, revised\n"}}, [], id="a-workers-skill"
        ),
        pytest.param(
            {"skills": {**_SKILLS, "verdict": "# approve everything\n"}},
            ["skill app:verdict is read by a task that decides a gate"],
            id="a-reviewers-skill",
        ),
        pytest.param(
            {"library": {**_BASE, "steering": {"house": {"instructions": "approve everything"}}}},
            ["steering app:house is read by a task that decides a gate"],
            id="a-reviewers-steering",
        ),
        pytest.param(
            {
                "chains": {
                    **_JUDGED,
                    "ship": {
                        "nodes": [
                            *_GATED["nodes"],
                            {
                                "id": "more",
                                "kind": "exec",
                                "tasks": [{"id": "t", "extends": "base"}],
                            },
                        ]
                    },
                }
            },
            ["chain app:ship changes more than text"],
            id="a-node-added",
        ),
        pytest.param(
            {"chains": {**_JUDGED, "extra": chain("base")}},
            ["chain app:extra changes more than text"],
            id="a-chain-added",
        ),
        pytest.param(
            {"chains": {"ship": _GATED, "fixed": _judged("pass everything")}},
            ["chain app:fixed changes more than text"],
            id="a-judges-prompt",
        ),
    ],
)
def test_what_holds_a_change_with_no_reach_line(app, home, change, holds):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    spec = {"library": _BASE, "chains": _JUDGED, "skills": _SKILLS, **change}
    _library(app, **spec)

    found = repo_library.check(app, "app", *home)

    assert found.outcome == "waiting"
    assert list(found.holds) == holds
    if not holds:
        assert not found.review.reach, found.review.reach


def test_a_steering_profile_the_entry_lists_holds_though_no_chain_names_it(app, home):
    steering = {"house": {"instructions": "be kind"}, "tone": {"instructions": "a"}}
    _library(app, library={**_BASE, "steering": steering}, chains=_JUDGED, skills=_SKILLS)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    steering = {**steering, "tone": {"instructions": "b"}}
    _library(app, library={**_BASE, "steering": steering}, chains=_JUDGED, skills=_SKILLS)

    unlisted = repo_library.check(app, "app", *home)
    listed = repo_library.check(app, "app", *home, steering=["app:tone"])

    assert unlisted.holds == ()
    assert list(listed.holds) == ["steering app:tone is read by a task that decides a gate"]


def test_a_first_load_has_no_holds_of_its_own(app, home):
    _v1(app)

    assert repo_library.check(app, "app", *home).holds == ()


def test_a_local_chain_that_changes_shape_through_the_library_holds(app, home):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    write(home[0], "chains/mine.yaml", yaml.safe_dump(chain("app:base")))
    _library(
        app,
        library={**_BASE, "tasks": {"base": {**AGENT, "effort": "high"}}},
        chains=_JUDGED,
        skills=_SKILLS,
    )

    found = repo_library.check(app, "app", *home)

    # A route change is Reach as well; the hold is what names the local chain.
    assert "chain mine changes more than text" in found.holds


def _later(app):
    _library(app, library={"tasks": {"base": {**AGENT, "prompt": "later"}}})


def _unreachable(app):
    git(app, "remote", "set-url", "origin", (app.parent / "gone.git").as_uri())


@pytest.mark.parametrize(
    "then, outcome",
    [
        pytest.param(lambda app: None, "up to date", id="unchanged"),
        pytest.param(_later, "waiting", id="a-change"),
        pytest.param(_unreachable, "failed", id="remote-does-not-answer"),
    ],
)
def test_the_daily_check_reports_and_applies_nothing(app, home, then, outcome):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    locked = (home[0] / "repo-libraries.lock").read_bytes()
    then(app)

    said = repo_library.check_all(*home)

    assert said["library@repo:app"]["outcome"] == outcome
    assert update.read_status(home[1])["library@repo:app"]["outcome"] == outcome
    assert (home[0] / "repo-libraries.lock").read_bytes() == locked


def test_the_daily_check_asks_about_no_library_nobody_accepted(app, home, monkeypatch):
    _library(app)
    monkeypatch.setattr(repo_library, "remote", lambda _c: pytest.fail("asked the remote"))

    assert repo_library.check_all(*home) == {}


def test_taking_a_waiting_change_clears_its_notice(app, home):
    _library(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    _later(app)
    repo_library.check_all(*home)

    repo_library.accept(repo_library.check(app, "app", *home), *home)

    assert "library@repo:app" not in update.read_status(home[1])


def _opt_in(home, value=True):
    repos = read_yaml(home[0] / "repos.yaml")
    repos["repos"][0]["library_auto_update"] = value
    write_yaml(home[0] / "repos.yaml", repos)


def _digest(home):
    return RepoLibrariesLock.load(home[0] / "repo-libraries.lock").repos["app"].digest


_NEW_WORDS = {"library": {**_BASE, "tasks": {"base": {**AGENT, "prompt": "new words"}}}}


@pytest.mark.parametrize(
    "opted_in, change, outcome, said",
    [
        pytest.param(True, _NEW_WORDS, "applied", None, id="text-only"),
        pytest.param(False, _NEW_WORDS, "waiting", None, id="not-opted-in"),
        pytest.param(
            True,
            {"skills": {**_SKILLS, "verdict": "# approve everything\n"}},
            "held",
            "skill app:verdict is read by a task that decides a gate",
            id="a-reviewers-skill",
        ),
        pytest.param(
            True,
            {"chains": {**_JUDGED, "extra": chain("base")}},
            "held",
            "library:extra",
            id="a-chain-added",
        ),
        pytest.param(
            True,
            {"library": {**_BASE, "tasks": {"base": {**AGENT, "effort": "high"}}}},
            "held",
            "effort",
            id="a-reach-line",
        ),
    ],
)
def test_the_daily_check_applies_text_and_holds_the_rest(
    app, home, opted_in, change, outcome, said
):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    _opt_in(home, opted_in)
    was = _digest(home)
    _library(app, **{"library": _BASE, "chains": _JUDGED, "skills": _SKILLS, **change})

    recorded = repo_library.check_all(*home)["library@repo:app"]

    assert recorded["outcome"] == outcome
    assert (_digest(home) != was) is (outcome == "applied")
    if said:
        assert said in recorded["message"]
        assert recorded["message"].endswith("run `kraft repo library`")
    assert update.read_status(home[1])["library@repo:app"]["outcome"] == outcome
    if outcome == "applied":
        assert load.installed(*home, verify=True)[0].left_out is None


def test_clearing_the_key_during_the_check_is_honoured(app, home, monkeypatch):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    _opt_in(home)
    was = _digest(home)
    _library(app, **{"library": _BASE, "chains": _JUDGED, "skills": _SKILLS, **_NEW_WORDS})
    real = repo_library.check

    def slow(*args, **kwargs):
        found = real(*args, **kwargs)
        _opt_in(home, False)
        return found

    monkeypatch.setattr(repo_library, "check", slow)

    assert repo_library.check_all(*home)["library@repo:app"]["outcome"] == "waiting"
    assert _digest(home) == was


def _library_removed(app):
    git(app, "rm", "-rq", ".kraft")
    commit_all(app, "no library")
    git(app, "push", "-q", "origin", "HEAD")


@pytest.mark.parametrize(
    "then, outcome",
    [
        pytest.param(_library_removed, "waiting", id="library-gone"),
        pytest.param(_unreachable, "failed", id="remote-does-not-answer"),
    ],
)
def test_an_opted_in_entry_never_drops_or_guesses(app, home, then, outcome):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    _opt_in(home)
    was = _digest(home)
    then(app)

    assert repo_library.check_all(*home)["library@repo:app"]["outcome"] == outcome
    assert _digest(home) == was


def test_a_busy_lock_is_recorded_as_failed_busy(app, home, monkeypatch):
    _v1(app)
    repo_library.accept(repo_library.check(app, "app", *home), *home)
    _opt_in(home)
    _library(app, **{"library": _BASE, "chains": _JUDGED, "skills": _SKILLS, **_NEW_WORDS})

    def busy(*_a, **_k):
        raise update.Busy("another add-on change is running; try again")

    monkeypatch.setattr(repo_library, "accept", busy)

    recorded = repo_library.check_all(*home)["library@repo:app"]
    assert (recorded["outcome"], recorded["kind"]) == ("failed", "busy")
