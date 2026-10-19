"""`kraft admin addon`: an author validates a collection without installing
anything; an operator adds collections and installs, updates and removes add-ons."""

import json
import socket

import pytest
from support.addons import (
    AGENT,
    accept_repo_library,
    chain,
    instance,
    make_collection,
    publish,
    repo_with_library,
)

from kraft import cli, update
from kraft.addons import fetch
from kraft.addons.config import AddonsLock
from kraft.cli import addon as addon_cli
from kraft.client import actions
from kraft.config import read_yaml, write_yaml

GOOD = {"release": {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}}


def _run(capsys, *argv):
    try:
        cli.main(["admin", "addon", *argv])
        code = 0
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    if isinstance(code, str):  # `SystemExit("error: ...")`: printed, and exit 1
        return 1, out.out + out.err + code
    return code, out.out + out.err


@pytest.mark.parametrize(
    ("addons", "sub", "running", "code", "says"),
    [
        (GOOD, "", None, 0, "release 1.0.0"),
        (GOOD, "addons/release", None, 0, "release 1.0.0"),
        (
            {"release": {**GOOD["release"], "chains": {"ship": chain("nope")}}},
            "",
            None,
            1,
            "extends no task named 'release:nope'",
        ),
        (
            {
                "release": {
                    **GOOD["release"],
                    "manifest": {"requires": {"kraft": "3.0", "harnesses": ["codex"]}},
                }
            },
            "",
            "2.4.0",
            1,
            "needs Kraft 3.x",
        ),
        (
            {"release": {**GOOD["release"], "manifest": {"category": "delivery"}}},
            "",
            None,
            0,
            "'category' is not read by Kraft",
        ),
        (GOOD, "", None, 0, "no Kraft home"),
        (
            {"release": {**GOOD["release"], "files": {"chains/Review.yaml": "nodes: []\n"}}},
            "",
            None,
            0,
            "chains/Review.yaml is not part of the add-on layout",
        ),
        (GOOD, "addons", None, 1, "holds neither"),
        (
            {"release": {"library": {"tasks": {"run": {"kind": "subprocess", "command": "make"}}}}},
            "",
            None,
            1,
            "may not carry a subprocess task",
        ),
        (
            {"release": {"files": {"library.yaml": "tasks: {'x:y': {kind: agent}}\n"}}},
            "",
            None,
            1,
            "addons/release/library.yaml",
        ),
    ],
    ids=[
        "collection",
        "single-addon",
        "lint-failure",
        "wrong-kraft-major",
        "unknown-key-warning",
        "no-home",
        "ignored-look-alike",
        "not-an-addon",
        "refused-content",
        "library-error-names-the-authors-file",
    ],
)
def test_validate(tmp_path, capsys, monkeypatch, addons, sub, running, code, says):
    if running is not None:
        monkeypatch.setattr(update, "installed", lambda: running)
    collection = make_collection(tmp_path, addons)
    got, out = _run(capsys, "validate", str(collection / sub))
    assert got == code and says in out, out


@pytest.mark.parametrize(
    "manifest_path",
    [
        pytest.param(".kraft/collection.json", id="collection"),
        pytest.param("addons/release/.kraft/addon.json", id="addon"),
    ],
)
def test_validate_names_a_broken_manifest_once(tmp_path, capsys, manifest_path):
    collection = make_collection(tmp_path, GOOD)
    broken = collection / manifest_path
    assert broken.is_file()
    broken.write_text("{")

    code, out = _run(capsys, "validate", str(collection))

    assert code == 1 and f"error: {broken}: " in out and out.count(str(broken)) == 1, out


def test_validate_writes_nothing_and_prints_json(tmp_path, capsys, monkeypatch):
    """`validate` installs nothing: the instance's home is not touched, with a
    home or without one. `--json` is the report itself."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KRAFT_HOME", str(home))
    collection = make_collection(tmp_path, GOOD)
    before = set(home.rglob("*"))
    code, out = _run(capsys, "validate", str(collection), "--json")
    report = json.loads(out)
    assert code == 0 and set(home.rglob("*")) == before
    assert [p["name"] for p in report["addons"]] == ["release"]
    assert report["addons"][0]["digest"].startswith("sha256:")
    assert report["problems"] == [] and report["skipped"]


# ── collections, install, update ──

ID = "release@acme"
TWO = {**GOOD, "tools": GOOD["release"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """This process's Kraft home, no add-on installed, and the collection
    `acme` (a git repository publishing `release` and `tools`) not yet added.
    `home.reloads` counts the reloads asked of a running server."""
    config_dir, addons_dir = instance(tmp_path)
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    reloads = []

    async def reload_addons():
        reloads.append(1)
        return {}

    monkeypatch.setattr(addon_cli.client, "reload_addons", reload_addons)

    class Home:
        config = config_dir
        addons = addons_dir
        repo = make_collection(tmp_path, TWO)

        def yaml(self):
            return read_yaml(config_dir / "addons.yaml")

        def locked(self, addon_id=ID):
            return AddonsLock.load(config_dir / "addons.lock").addons.get(addon_id)

        def written(self):
            lock = config_dir / "addons.lock"
            store = addons_dir / "store"
            return (
                (config_dir / "addons.yaml").read_text(),
                lock.read_text() if lock.exists() else "",
                sorted(p.name for p in store.iterdir()) if store.is_dir() else [],
            )

    Home.reloads = reloads
    return Home()


@pytest.fixture
def added(home, capsys):
    assert _run(capsys, "collection", "add", home.repo.as_uri())[0] == 0
    return home


@pytest.fixture
def installed(added, capsys):
    assert _run(capsys, "install", ID, "-y")[0] == 0
    added.reloads.clear()
    return added


@pytest.mark.parametrize(
    "source, flags, writes",
    [
        ("acme/kraft-addons", [], {"git": "https://github.com/acme/kraft-addons.git"}),
        ("DIRECTORY", [], {"path": "DIRECTORY"}),
        (
            "URL",
            ["--ref", "main", "--auto-update"],
            {"git": "URL", "ref": "main", "auto_update": True},
        ),
    ],
    ids=["owner-repo", "directory", "ref"],
)
def test_collection_add_writes_a_full_source(home, capsys, monkeypatch, source, flags, writes):
    """`addons.yaml` never depends on a host it does not name."""
    names = {"DIRECTORY": str(home.repo), "URL": home.repo.as_uri()}
    if source == "DIRECTORY":  # typed relative to where the operator stands
        monkeypatch.chdir(home.repo.parent)
        source = home.repo.name
    real = fetch.fetch
    # GitHub is not reachable from a test: what would be fetched there is here.
    monkeypatch.setattr(
        fetch, "fetch", lambda root, name, url, ref: real(root, name, home.repo.as_uri(), ref)
    )

    code, out = _run(capsys, "collection", "add", names.get(source, source), *flags)

    assert (code, out.strip()) == (0, "added collection acme")
    assert home.yaml()["collections"] == {
        "acme": {key: names.get(value, value) for key, value in writes.items()}
    }
    assert home.yaml()["addons"] == {} and not (home.addons / "store").exists()


def test_collection_add_refuses_a_taken_name(added, capsys, tmp_path):
    other = make_collection(tmp_path / "elsewhere", GOOD)
    before = added.written()

    code, out = _run(capsys, "collection", "add", other.as_uri())

    assert code != 0 and f"collection acme is already added from {added.repo.as_uri()}" in out
    assert added.written() == before
    mirrors = [m.name for m in (added.addons / "mirrors").iterdir()]
    assert mirrors == [fetch.mirror_path(added.addons, "acme", added.repo.as_uri()).name]
    assert _run(capsys, "collection", "add", added.repo.as_uri())[0] == 0  # the same source again
    assert added.written() == before


def test_install_as_alias(added, capsys):
    code, out = _run(capsys, "install", ID, "--as", "rel", "-y")

    assert code == 0 and "release@acme: applied" in out and "release@acme new, 1.0.0" in out
    assert added.yaml()["addons"] == {ID: {"as": "rel"}}
    assert added.locked().namespace == "rel"
    assert added.reloads == [1]
    code, out = _run(capsys, "list", "--json")
    assert [(row["id"], row["namespace"], row["left_out"]) for row in json.loads(out)] == [
        (ID, "rel", None)
    ]


@pytest.mark.parametrize(
    "flags, code, takes",
    [([], 1, False), (["--re-install"], 0, True)],
    ids=["refused-without-flag", "takes-newest-commit"],
)
def test_install_re_install(installed, capsys, flags, code, takes):
    head = publish(installed.repo, "release", library={"tasks": {"base": AGENT, "more": AGENT}})
    before = installed.written()

    got, out = _run(capsys, "install", ID, "-y", *flags)

    assert got == code, out
    assert (installed.locked().commit == head) == takes
    if not takes:
        assert "already installed" in out and "--re-install" in out
        assert installed.written() == before


def test_a_refused_install_writes_nothing(added, capsys):
    publish(added.repo, "release", library={"tasks": {"base": {**AGENT, "kind": "subprocess"}}})
    before = added.written()

    code, out = _run(capsys, "install", ID, "-y")

    assert code == 1 and "release@acme: refused" in out and "subprocess" in out
    assert added.written() == before and added.reloads == []


@pytest.mark.parametrize(
    "prepare, argv, code, says, collection, entry",
    [
        ([], ["collection", "auto-update", "acme", "on"], 0, "acme: auto-update on", True, True),
        ([], ["auto-update", ID, "on"], 0, "auto-update on", None, {"auto_update": True}),
        (
            [["collection", "auto-update", "acme", "on"]],
            ["auto-update", ID, "on"],
            1,
            "acme auto-updates every add-on; drop auto_update here",
            True,
            True,
        ),
        (
            [["auto-update", ID, "on"]],
            ["collection", "auto-update", "acme", "on"],
            1,
            "these add-ons set their own auto_update: release@acme",
            None,
            {"auto_update": True},
        ),
        (
            [["auto-update", ID, "on"]],
            ["auto-update", ID, "off"],
            0,
            "auto-update off",
            None,
            True,
        ),
    ],
    ids=[
        "collection-on",
        "addon-on",
        "addon-on-under-auto-collection-refused",
        "collection-on-with-addon-settings-refused",
        "addon-off-is-the-short-form-again",
    ],
)
def test_auto_update_verbs(installed, capsys, prepare, argv, code, says, collection, entry):
    for earlier in prepare:
        assert _run(capsys, *earlier)[0] == 0

    got, out = _run(capsys, *argv)

    assert got == code and says in out, out
    written = installed.yaml()
    assert (written["collections"]["acme"].get("auto_update"), written["addons"][ID]) == (
        collection,
        entry,
    )


def test_collection_update_never_changes_the_lock(installed, capsys):
    publish(installed.repo, "release", version="1.1.0")
    before = installed.written()

    assert _run(capsys, "collection", "update", "acme")[0] == 0

    assert installed.written() == before
    code, out = _run(capsys, "collection", "list")
    assert code == 0 and "release  (installed)" in out and "\n  tools\n" in out + "\n"


def test_collection_remove_refused_while_installed(installed, capsys):
    mirror = fetch.mirror_path(installed.addons, "acme", installed.repo.as_uri())
    code, out = _run(capsys, "collection", "remove", "acme")
    assert code != 0 and "still has add-ons installed: release@acme" in out
    assert "acme" in installed.yaml()["collections"] and mirror.is_dir()

    assert _run(capsys, "uninstall", ID)[0] == 0
    assert _run(capsys, "collection", "remove", "acme")[0] == 0
    assert installed.yaml()["collections"] == {} and not mirror.exists()


@pytest.mark.parametrize("verb", ["uninstall", "disable"])
def test_removing_a_referenced_addon_is_refused(installed, capsys, verb):
    chains = installed.config / "chains"
    write_yaml(chains / "mine.yaml", chain("release:base"))
    write_yaml(
        installed.config / "repos.yaml",
        {"repos": [{"path": "/r", "default_chain": "release:ship"}]},
    )
    before = installed.written()

    code, out = _run(capsys, verb, ID)

    assert code != 0 and f"cannot {verb} release@acme" in out
    assert "chains/mine.yaml: nodes[n].tasks[t]: extends 'release:base'" in out
    assert "repos.yaml: repos[0]: default_chain 'release:ship'" in out
    assert installed.written() == before and installed.reloads == []

    (chains / "mine.yaml").unlink()
    (installed.config / "repos.yaml").unlink()
    assert _run(capsys, verb, ID)[0] == 0
    assert installed.reloads == [1]
    if verb == "uninstall":
        assert installed.yaml()["addons"] == {} and installed.locked() is None
    else:
        assert installed.yaml()["addons"] == {ID: False} and installed.locked() is not None
        assert _run(capsys, "enable", ID)[0] == 0 and installed.yaml()["addons"] == {ID: True}


def test_removing_an_addon_a_chain_extends_is_refused(installed, capsys):
    write_yaml(installed.config / "chains" / "wraps.yaml", {"extends": "release:ship"})
    before = installed.written()

    code, out = _run(capsys, "uninstall", ID)

    assert code != 0 and "chains/wraps.yaml: extends 'release:ship'" in out
    assert installed.written() == before


def _a_new_version(home):
    publish(home.repo, "release", version="1.1.0")


def _a_refused_version(home):
    publish(
        home.repo,
        "release",
        version="1.1.0",
        library={"tasks": {"base": {**AGENT, "kind": "subprocess"}}},
    )


def _an_unreachable_collection(home):
    written = home.yaml()
    written["collections"]["acme"]["git"] = (home.config / "nowhere").as_uri()
    write_yaml(home.config / "addons.yaml", written)


@pytest.mark.parametrize(
    "happens, code, says",
    [
        (None, 0, "release@acme: current"),
        (_a_new_version, 1, "release@acme: update waiting"),
        (_a_refused_version, 2, "release@acme: refused"),
        (_an_unreachable_collection, 3, "release@acme: failed"),
    ],
    ids=["up-to-date", "update-waiting", "refused", "cannot-check"],
)
def test_update_check_exit_codes(installed, capsys, happens, code, says):
    if happens:
        happens(installed)
    before = installed.written()

    got, out = _run(capsys, "update", "--check")

    assert got == code and says in out, out
    assert "no terminal" not in out  # nobody was going to be asked
    assert installed.written() == before and installed.reloads == []


def test_update_check_notes_changed_files_at_the_same_version(installed, capsys):
    """A forgotten version bump gets noticed, and is not an update."""
    publish(installed.repo, "release", library={"tasks": {"base": AGENT, "more": AGENT}})

    code, out = _run(capsys, "update", "--check")

    assert code == 0
    assert "version 1.0.0 unchanged, but its files changed at" in out and "--re-install" in out


def test_no_terminal_without_yes_declines(installed, capsys):
    """pytest's stdin is no terminal: nobody can be asked."""
    publish(installed.repo, "release", version="1.1.0")
    before = installed.written()

    code, out = _run(capsys, "update")

    assert code == 1 and "no terminal to ask at; pass -y to accept" in out
    assert installed.written() == before

    code, out = _run(capsys, "update", "-y")
    assert code == 0 and installed.locked().version == "1.1.0" and installed.reloads == [1]


@pytest.mark.parametrize(
    "argv",
    [
        ["collection", "add", "acme/kraft-addons"],
        ["collection", "auto-update", "acme", "on"],
        ["collection", "remove", "acme"],
        ["collection", "update"],
        ["install", ID, "-y"],
        ["update", "-y"],
        ["auto-update", ID, "on"],
        ["enable", ID],
        ["disable", ID],
        ["uninstall", ID],
    ],
    ids=[
        "collection-add",
        "collection-auto-update",
        "collection-remove",
        "collection-update",
        "install",
        "update",
        "auto-update",
        "enable",
        "disable",
        "uninstall",
    ],
)
def test_a_worker_cannot_change_addons(installed, capsys, monkeypatch, argv):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    before = installed.written()

    code, out = _run(capsys, *argv)

    assert code != 0 and "a worker does not change the instance's add-ons" in out
    assert installed.written() == before
    assert _run(capsys, "update", "--check")[0] == 0  # reading is not changing
    assert _run(capsys, "list")[0] == 0


def test_urls_are_redacted(home, capsys):
    """An ssh URL may carry a user; no verb prints it, git's own error included."""
    secret = "ssh://hunter2@127.0.0.1:1/acme.git"
    write_yaml(
        home.config / "addons.yaml", {"collections": {"acme": {"git": secret}}, "addons": {}}
    )

    shown = [
        _run(capsys, "collection", "list")[1],
        _run(capsys, "collection", "update")[1],
        _run(capsys, "install", ID, "-y")[1],
        _run(capsys, "collection", "add", secret)[1],
        _run(capsys, "collection", "add", str(home.repo))[1],  # names where acme came from
    ]

    assert "ssh://127.0.0.1:1/acme.git" in shown[0]
    assert "collection acme is already added from ssh://127.0.0.1:1/acme.git" in shown[4]
    assert all("hunter2" not in text for text in shown), shown


@pytest.mark.parametrize(
    "verb", [["list"], ["collection", "list"]], ids=["list", "collection-list"]
)
def test_an_unreadable_addons_yaml_is_an_error_not_a_traceback(home, capsys, verb):
    """The shared flow refuses with `flow.Refusal`; the CLI says it as every
    verb here does: `error: ...` and exit 1."""
    (home.config / "addons.yaml").write_text("collections: [\n")

    code, out = _run(capsys, *verb)

    assert code == 1 and out.startswith("error: ") and "addons.yaml" in out, out


def test_added_collection_json_does_not_echo_the_user(home, capsys, monkeypatch):
    real = fetch.fetch
    # An ssh URL is the one a config accepts with a user in it; fetch the local repo instead.
    local = home.repo.as_uri()
    monkeypatch.setattr(
        fetch, "fetch", lambda addons_dir, name, url, ref: real(addons_dir, name, local, ref)
    )

    code, out = _run(capsys, "collection", "add", "ssh://hunter2@example.com/acme.git", "--json")

    assert code == 0, out
    assert json.loads(out) == {
        "collection": "acme",
        "git": "ssh://example.com/acme.git",
    }


def test_update_json_is_the_results_with_each_review(installed, capsys):
    publish(
        installed.repo,
        "release",
        version="1.1.0",
        library={"tasks": {"base": AGENT, "more": AGENT}},
    )

    code, _ = _run(capsys, "update", "--check", "--json")
    # The review is for a reader, on stderr; stdout is the payload alone.
    cli_out = capsys.readouterr()
    try:
        cli.main(["admin", "addon", "update", "-y", "--json"])
    except SystemExit:
        pass
    payload = json.loads(capsys.readouterr().out)

    assert code == 1 and cli_out.out == ""
    assert [(row["addon"], row["outcome"]) for row in payload] == [(ID, "applied")]
    assert payload[0]["review"] == {
        "old_version": "1.0.0",
        "new_version": "1.1.0",
        "reach": [],
        "content": ["component tasks.more added"],
    }


@pytest.mark.parametrize("server", ["none", "refuses", "leaves-it-out"])
def test_a_missing_server_is_not_a_warning(installed, capsys, monkeypatch, server):
    """The next start reads the new lock, so no server is not a problem; one
    that answers an error is."""
    if server == "none":  # the real call, at a port nothing listens on
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            monkeypatch.setenv("KRAFT_PORT", str(probe.getsockname()[1]))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        monkeypatch.setattr(addon_cli.client, "reload_addons", actions.reload_addons)
    else:

        async def refused():
            if server == "refuses":
                raise ValueError("kraft 500: boom")
            return {"invalid_templates": {f"add-on {ID}": "its store is gone", "chains/x": "no"}}

        monkeypatch.setattr(addon_cli.client, "reload_addons", refused)

    code, out = _run(capsys, "disable", ID)

    assert code == 0 and f"{ID}: disabled" in out
    assert ("warning: the running server did not reload: kraft 500: boom" in out) is (
        server == "refuses"
    )
    assert (f"warning: the running server did not load add-on {ID}: its store is gone" in out) is (
        server == "leaves-it-out"
    )
    assert "no Kraft server" not in out and "chains/x" not in out


def _the_same_collection(home, tmp_path):
    return home.repo


def _another_collection_with_the_name(home, tmp_path):
    return make_collection(tmp_path / "elsewhere", GOOD, name="other")


def _an_addon_this_instance_cannot_meet(home, tmp_path):
    needs = {"requires": {"kraft": "2", "harnesses": ["codex"], "profiles": ["deep"]}}
    addon = {"deploy": {**GOOD["release"], "manifest": needs}}
    return make_collection(tmp_path / "elsewhere", addon, name="other")


def _a_version_that_breaks_a_local_chain(home, tmp_path):
    write_yaml(home.config / "chains" / "mine.yaml", chain("release:base"))
    publish(home.repo, "release", version="1.1.0", library={"tasks": {"other": AGENT}}, chains={})
    (home.repo / "addons" / "release" / "chains" / "ship.yaml").unlink()
    return home.repo


def _a_version_that_breaks_a_repo_library_chain(home, tmp_path):
    """A connected repository's chain extends `release:ship`, which the new
    version drops: a warning, not a problem, as the update holds for it."""
    app = repo_with_library(
        tmp_path,
        library={"addons": [ID], "tasks": {"base": AGENT}},
        chains={"ship": {"extends": "release:ship"}},
    )
    accept_repo_library(home.config, home.addons, app, "app")
    publish(home.repo, "release", version="1.1.0", chains={})
    (home.repo / "addons" / "release" / "chains" / "ship.yaml").unlink()
    return home.repo


def _a_skill_into_an_installed_addon(home, tmp_path):
    reaches = {**AGENT, "skill": "release:notes"}
    addon = {"deploy": {"library": {"tasks": {"base": reaches}}, "chains": {"go": chain("base")}}}
    return make_collection(tmp_path / "elsewhere", addon, name="other")


@pytest.mark.parametrize(
    "collection, code, says",
    [
        (_the_same_collection, 0, "release 1.0.0"),
        (_another_collection_with_the_name, 1, "namespace 'release' is taken by release@acme"),
        (_an_addon_this_instance_cannot_meet, 1, "requires agent profile 'deep'"),
        (_a_version_that_breaks_a_local_chain, 1, "chain mine would stop resolving"),
        (
            _a_version_that_breaks_a_repo_library_chain,
            0,
            "warning: {repo}/addons/release: chain app:ship would stop resolving",
        ),
        (_a_skill_into_an_installed_addon, 1, "names another installed Kraft add-on"),
    ],
    ids=[
        "same-id-is-not-a-clash",
        "namespace-taken",
        "requires-unmet",
        "breaks-a-chain",
        "breaks-a-repo-library-chain",
        "dependency",
    ],
)
def test_validate_against_a_kraft_home(installed, capsys, tmp_path, collection, code, says):
    """With a Kraft home, `validate` is the install's own checks, and still
    installs nothing."""
    path = collection(installed, tmp_path)
    before = installed.written()

    got, out = _run(capsys, "validate", str(path))

    assert got == code and says.format(repo=path) in out, out
    assert "no Kraft home was used" not in out
    assert installed.written() == before and installed.reloads == []


def test_uninstall_forgets_the_addons_last_auto_update(installed, capsys):
    from kraft.addons import update as addon_update

    status = installed.addons / addon_update.STATUS_FILE
    status.write_text(json.dumps({ID: {"outcome": "held"}, "other@acme": {"outcome": "failed"}}))

    assert _run(capsys, "uninstall", ID)[0] == 0

    assert addon_update.read_status(installed.addons) == {"other@acme": {"outcome": "failed"}}
