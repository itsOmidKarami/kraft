"""`kraft admin addon validate`: an author checks a collection or an add-on
without installing anything, and a child add-on against its parent. The
instance-side rows are `test_addon`'s `test_validate_against_a_kraft_home`."""

import json

import pytest
from support.addons import chain, install, instance, make_collection, publish

from cli.test_addon import GOOD, TEAM, _run
from kraft import update

#: The message validate gives for a parent it finds nowhere.
NOT_FOUND = (
    "its parent release@acme could not be found in PATH, --with or this instance; "
    "pass its collection with --with"
)


def _team(**library):
    """`team`, building on `release@acme` 1.x, its library laid over TEAM's."""
    return {**TEAM, "library": {**TEAM["library"], **library}}


def _parent_in_path(tmp_path, monkeypatch, team=None):
    return [str(make_collection(tmp_path, {**GOOD, "team": team or TEAM}))]


def _grandparent_in_path(tmp_path, monkeypatch):
    """`team` builds on `dept`, which builds on `release`; `team` reaches
    `release:base` through `dept`."""
    dept = {
        "library": {"tasks": {"lint": {"extends": "release:base"}}},
        "manifest": TEAM["manifest"],
    }
    on_dept = {"requires": {**TEAM["manifest"]["requires"], "addons": {"dept@acme": "1"}}}
    team = {**_team(tasks={"scan": {"extends": "dept:base"}}), "manifest": on_dept}
    return [str(make_collection(tmp_path, {**GOOD, "dept": dept, "team": team}))]


def _parent_via_with(tmp_path, monkeypatch):
    corp = make_collection(tmp_path, GOOD)
    team = make_collection(tmp_path, {"team": TEAM}, name="payments")
    return [str(team), "--with", str(corp)]


def _parent_nowhere(tmp_path, monkeypatch):
    return [str(make_collection(tmp_path, {"team": TEAM}, name="payments"))]


def _broken_against_parent(tmp_path, monkeypatch):
    team = _team(tasks={"scan": {"extends": "release:nope"}})
    return _parent_in_path(tmp_path, monkeypatch, team)


def _removes_what_it_inherits(tmp_path, monkeypatch):
    return _parent_in_path(tmp_path, monkeypatch, _team(remove={"chains": ["ship"]}))


def _a_home(tmp_path, monkeypatch, collections=None):
    config_dir, addons_dir = instance(tmp_path, collections)
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    return config_dir, addons_dir


def _parent_installed(tmp_path, monkeypatch):
    """`release` 1.0.0 is installed; its collection has since moved to 2.0.0,
    which `team` does not accept: the installed version is the parent."""
    corp = make_collection(tmp_path, GOOD)
    install(*_a_home(tmp_path, monkeypatch), corp, "release")
    publish(corp, "release", version="2.0.0")
    return _parent_nowhere(tmp_path, monkeypatch)


def _parent_in_an_added_collection(tmp_path, monkeypatch):
    """The instance has added `acme` but not installed `release`: its current
    version there is the parent."""
    corp = make_collection(tmp_path, GOOD)
    _a_home(tmp_path, monkeypatch, {"acme": {"git": corp.as_uri()}})
    return _parent_nowhere(tmp_path, monkeypatch)


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
        (_parent_in_path, "", None, 0, "team 1.0.0"),
        (_grandparent_in_path, "", None, 0, "team 1.0.0"),
        (_parent_via_with, "", None, 0, "team 1.0.0"),
        (_parent_nowhere, "", None, 1, NOT_FOUND),
        (_broken_against_parent, "", None, 1, "nope"),
        (_removes_what_it_inherits, "", None, 0, "team 1.0.0"),
        (_parent_installed, "", None, 0, "team 1.0.0"),
        (_parent_in_an_added_collection, "", None, 0, "team 1.0.0"),
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
        "child-with-parent-in-path",
        "child-with-grandparent-in-path",
        "child-with-parent-via-with",
        "child-parent-not-found",
        "child-broken-against-parent",
        "child-removes-what-it-inherits",
        "child-against-instance",
        "child-against-instance-collection",
    ],
)
def test_validate(tmp_path, capsys, monkeypatch, addons, sub, running, code, says):
    if running is not None:
        monkeypatch.setattr(update, "installed", lambda: running)
    # A row that is a function lays out its own collections (and Kraft home)
    # and returns validate's arguments.
    if callable(addons):
        argv = addons(tmp_path, monkeypatch)
    else:
        argv = [str(make_collection(tmp_path, addons) / sub)]
    got, out = _run(capsys, "validate", *argv)
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
    home or without one. `--json` is the report itself, naming each add-on's
    parent."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KRAFT_HOME", str(home))
    collection = make_collection(tmp_path, {**GOOD, "team": TEAM})
    before = set(home.rglob("*"))
    code, out = _run(capsys, "validate", str(collection), "--json")
    report = json.loads(out)
    assert code == 0 and set(home.rglob("*")) == before
    assert {p["name"]: p["parent"] for p in report["addons"]} == {
        "release": None,
        "team": "release@acme",
    }
    assert all(p["digest"].startswith("sha256:") for p in report["addons"])
    assert report["problems"] == [] and report["skipped"]
