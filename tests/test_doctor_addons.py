"""`kraft admin doctor`'s add-on rows, read from the files with no server."""

import json

import pytest
from support.addons import AGENT, chain, drop_store, edit_addons_yaml, instance, make_collection

from kraft import doctor
from kraft.addons import update
from kraft.config import write_yaml

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
