"""`kraft admin doctor`'s add-on rows, read from the files with no server."""

import json

import pytest
from support.addons import (
    AGENT,
    accept_repo_library,
    chain,
    drop_store,
    edit_addons_yaml,
    instance,
    make_collection,
    repo_with_library,
)

from kraft import doctor
from kraft.addons import update
from kraft.config import read_yaml, write_yaml

ID = "release@acme"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A config directory with `release@acme` installed, as this process's own."""
    release = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    repo = make_collection(tmp_path, {"release": release})
    config_dir, addons_dir = instance(tmp_path, {"acme": {"git": repo.as_uri()}})
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    update.update(
        [ID],
        accept=lambda review: True,
        entries={ID: True},
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    (store,) = (addons_dir / "store").iterdir()
    return config_dir, addons_dir, store, repo


def _rows(config_dir):
    return {row["name"]: row for row in doctor._addon_checks(config_dir)}


def test_a_healthy_instance_says_how_many_addons_load(installed):
    rows = _rows(installed[0])
    assert (rows["add-ons"]["ok"], rows["add-ons"]["warn"], rows["add-ons"]["detail"]) == (
        True,
        False,
        "1 add-on(s) load",
    )
    assert rows["add-on updates"]["detail"] == "0 add-on(s) auto-update"


def _store_gone(config_dir, store, repo):
    drop_store(store)


def _files_do_not_read(config_dir, store, repo):
    (config_dir / "addons.yaml").write_text("addons: [\n")


def _ref_changed(config_dir, store, repo):
    edit_addons_yaml(config_dir, lambda w: w["collections"]["acme"].update(ref="main"))


def _url_changed(config_dir, store, repo):
    edit_addons_yaml(
        config_dir, lambda w: w["collections"]["acme"].update(git=(repo.parent / "fork").as_uri())
    )


def _listed_not_installed(config_dir, store, repo):
    edit_addons_yaml(config_dir, lambda w: w["addons"].update({"tools@acme": True}))


def _a_directory_collection(config_dir, store, repo):
    edit_addons_yaml(config_dir, lambda w: w["collections"].update(local={"path": str(repo)}))


@pytest.mark.parametrize(
    "happens, ok, warn, says",
    [
        (_store_gone, False, False, "release@acme: its store is missing"),
        (_files_do_not_read, False, False, "addons.yaml"),
        (_ref_changed, True, True, "release@acme: ref change pending"),
        (_url_changed, True, True, "release@acme: collection URL change pending"),
        (_listed_not_installed, True, True, "tools@acme is not installed"),
        (_a_directory_collection, True, True, "collection local is a local directory"),
    ],
    ids=[
        "left-out",
        "unreadable",
        "ref-pending",
        "url-pending",
        "not-installed",
        "directory-collection",
    ],
)
def test_the_addons_row(installed, happens, ok, warn, says):
    config_dir, _addons_dir, store, repo = installed
    happens(config_dir, store, repo)

    row = _rows(config_dir)["add-ons"]

    assert (row["ok"], row["warn"]) == (ok, warn) and says in row["detail"], row


@pytest.mark.parametrize(
    "entry, says",
    [
        ({"outcome": "held", "message": "gate removed"}, "release@acme: held: gate removed"),
        (
            {"outcome": "failed", "kind": "auth", "message": "denied"},
            "release@acme: failed (auth): denied; a server run by launchd or systemd may lack",
        ),
        ({"outcome": "applied"}, "no allowed_tools, sandbox or maxima"),
    ],
    ids=["held", "failed-auth", "nothing-bounds-an-unattended-update"],
)
def test_the_addon_updates_row_warns_and_never_fails(installed, entry, says):
    config_dir, addons_dir, _store, _repo = installed
    edit_addons_yaml(config_dir, lambda w: w["addons"].update({ID: {"auto_update": True}}))
    (addons_dir / update.STATUS_FILE).write_text(json.dumps({ID: entry}))

    row = _rows(config_dir)["add-on updates"]

    assert (row["ok"], row["warn"]) == (True, True) and says in row["detail"], row


def test_a_bounded_instance_with_a_current_addon_has_nothing_to_say(installed):
    config_dir, addons_dir, _store, _repo = installed
    edit_addons_yaml(config_dir, lambda w: w["addons"].update({ID: {"auto_update": True}}))
    write_yaml(config_dir / "policy.yaml", {"maxima": {"tasks": {"budget_usd": 5}}})
    (addons_dir / update.STATUS_FILE).write_text(json.dumps({ID: {"outcome": "up to date"}}))

    row = _rows(config_dir)["add-on updates"]

    assert (row["warn"], row["detail"]) == (False, "1 add-on(s) auto-update")


@pytest.mark.parametrize(
    "status, expected",
    [
        pytest.param(None, "1 loaded", id="loaded"),
        pytest.param(
            {"outcome": "waiting", "message": "component tasks.base changed"},
            "app: a change is waiting (component tasks.base changed); run `kraft repo library`",
            id="waiting",
        ),
        pytest.param(
            {
                "outcome": "held",
                "message": "chain app:extra changes more than text; run `kraft repo library`",
            },
            "app: a change is held "
            "(chain app:extra changes more than text; run `kraft repo library`)",
            id="held",
        ),
        pytest.param({"outcome": "applied"}, "1 loaded", id="applied"),
        pytest.param(
            {"outcome": "failed", "kind": "auth", "message": "Permission denied"},
            "app: could not be checked (auth: Permission denied)",
            id="failed",
        ),
        pytest.param(
            "named",
            "chains/mine.yaml: nodes[n].tasks[t]: extends app:base is in app's repo library, "
            "visible only to items in app",
            id="named-from-outside",
        ),
    ],
)
def test_the_repo_libraries_row(tmp_path, monkeypatch, status, expected):
    repo = repo_with_library(tmp_path)
    config_dir, addons_dir = instance(tmp_path)
    accept_repo_library(config_dir, addons_dir, repo, "app")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    if status == "named":
        (config_dir / "chains").mkdir(exist_ok=True)
        (config_dir / "chains" / "mine.yaml").write_text(json.dumps(chain("app:base")))
    elif status:
        (addons_dir / "status.json").write_text(json.dumps({"library@repo:app": status}))

    row = _rows(config_dir)["repo libraries"]

    assert row["ok"] is (status != "named")
    assert expected in row["detail"]


@pytest.mark.parametrize(
    "policy, warns",
    [
        pytest.param({}, True, id="no-bounds"),
        pytest.param({"maxima": {"budget_usd": 5}}, False, id="maxima"),
        pytest.param({"defaults": {"sandbox": {"backend": "docker"}}}, False, id="sandbox"),
    ],
)
def test_the_repo_libraries_row_warns_about_unbounded_auto_update(
    tmp_path, monkeypatch, policy, warns
):
    repo = repo_with_library(tmp_path)
    config_dir, addons_dir = instance(tmp_path)
    accept_repo_library(config_dir, addons_dir, repo, "app")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    repos = read_yaml(config_dir / "repos.yaml")
    repos["repos"][0]["library_auto_update"] = True
    write_yaml(config_dir / "repos.yaml", repos)
    write_yaml(config_dir / "policy.yaml", policy)

    row = _rows(config_dir)["repo libraries"]

    assert row["ok"] is True
    assert row["warn"] is warns
    assert (
        "auto-update(s) on an instance with no allowed_tools, sandbox" in row["detail"]
    ) is warns


def test_a_repo_library_that_does_not_load_fails_its_row(tmp_path, monkeypatch):
    repo = repo_with_library(tmp_path)
    config_dir, addons_dir = instance(tmp_path)
    accept_repo_library(config_dir, addons_dir, repo, "app")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    write_yaml(config_dir / "harnesses.yaml", {"harnesses": {}})

    row = _rows(config_dir)["repo libraries"]

    assert row["ok"] is False
    assert "app: requires harness 'codex'" in row["detail"]
