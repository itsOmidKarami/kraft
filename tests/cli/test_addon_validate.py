"""`kraft admin addon validate`: an author checks a collection or an add-on
without installing anything, and a child add-on against its parent. The
instance-side rows are `test_addon`'s `test_validate_against_a_kraft_home`."""

import json
import tempfile

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


def _parent_nowhere_and_refused_content(tmp_path, monkeypatch):
    team = _team(tasks={**TEAM["library"]["tasks"], "run": {"kind": "subprocess"}})
    return [str(make_collection(tmp_path, {"team": team}, name="payments"))]


def _a_cycle(tmp_path, monkeypatch):
    """`team` builds on `release`, which builds on `team`."""
    on_team = {"requires": {**TEAM["manifest"]["requires"], "addons": {"team@acme": "1"}}}
    release = {**GOOD["release"], "manifest": on_team}
    return [str(make_collection(tmp_path, {"release": release, "team": TEAM}))]


def _with_holds_neither(tmp_path, monkeypatch):
    return [*_parent_via_with(tmp_path, monkeypatch), "--with", str(tmp_path / "nothing")]


def _unreadable_with_before_the_parent(tmp_path, monkeypatch):
    broken = tmp_path / "broken"
    (broken / ".kraft").mkdir(parents=True)
    (broken / ".kraft" / "collection.json").write_text("{")
    team, _, corp = _parent_via_with(tmp_path, monkeypatch)
    return [team, "--with", str(broken), "--with", corp]


def _broken_against_parent(tmp_path, monkeypatch):
    team = _team(tasks={"scan": {"extends": "release:nope"}})
    return _parent_in_path(tmp_path, monkeypatch, team)


def _parent_broken(tmp_path, monkeypatch):
    """`release`, the parent, does not itself resolve: `team` builds on it."""
    release = {**GOOD["release"], "library": {"tasks": {"base": {"extends": "nope"}}}}
    corp = make_collection(tmp_path, {"release": release})
    return [str(make_collection(tmp_path, {"team": TEAM}, name="payments")), "--with", str(corp)]


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
        (
            _parent_nowhere_and_refused_content,
            "",
            None,
            1,
            (NOT_FOUND, "may not carry a subprocess task"),
        ),
        (
            _a_cycle,
            "",
            None,
            1,
            "addons/team: its ancestry team@acme -> release@acme -> team@acme is a cycle",
        ),
        (_with_holds_neither, "", None, 1, "nothing: holds neither .kraft/collection.json"),
        (_unreadable_with_before_the_parent, "", None, 0, "team 1.0.0"),
        (_broken_against_parent, "", None, 1, "nope"),
        (
            _parent_broken,
            "",
            None,
            1,
            (
                # The parent's failure is its own, named by where it was read from,
                # once, and not as a copy under the child or a deleted scratch path.
                "<tmp>/acme-kraft/addons/release: "
                "<tmp>/acme-kraft/addons/release/chains/ship.yaml: "
                "n.main.t: extends no task "
                "named 'release:nope'",
                "!ship.yaml: n.main.t: extends no task named 'team:nope'",
                "!<scratch>",
            ),
        ),
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
        "child-parent-not-found-and-refused-content",
        "child-in-a-cycle",
        "with-dir-holds-neither",
        "with-dir-unreadable-before-the-parent",
        "child-broken-against-parent",
        "parent-broken-not-blamed-on-child",
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
    # "<tmp>" is this test's directory; a "!" row must not be said, and
    # "<scratch>" is where validate's own temporary copies are made.
    said = [
        s.replace("<tmp>", str(tmp_path)).replace("<scratch>", tempfile.gettempdir() + "/tmp")
        for s in (says if isinstance(says, tuple) else [says])
    ]
    assert got == code, out
    for s in said:
        assert (s[1:] not in out) if s.startswith("!") else (s in out), out


def test_validate_against_an_instance_writes_nothing(tmp_path, capsys, monkeypatch):
    """A parent read from the instance's collection is fetched into a
    temporary directory: the home's config and add-ons are not touched."""
    argv = _parent_in_an_added_collection(tmp_path, monkeypatch)

    def files():
        return {
            p: p.read_bytes()
            for p in [*(tmp_path / "config").rglob("*"), *(tmp_path / "run").rglob("*")]
            if p.is_file()
        }

    before = files()
    got, out = _run(capsys, "validate", *argv)
    assert got == 0 and files() == before, out


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
