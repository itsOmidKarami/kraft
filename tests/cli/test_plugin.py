"""`kraft admin plugin validate`: an author checks a collection or a plugin
directory without installing anything."""

import json

import pytest
from support.plugins import AGENT, chain, make_collection

from kraft import cli, update

GOOD = {"release": {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}}


def _run(capsys, *argv):
    try:
        cli.main(["admin", "plugin", "validate", *argv])
        code = 0
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out + out.err


@pytest.mark.parametrize(
    ("plugins", "sub", "running", "code", "says"),
    [
        (GOOD, "", None, 0, "release 1.0.0"),
        (GOOD, "plugins/release", None, 0, "release 1.0.0"),
        (
            {"release": {**GOOD["release"], "chains": {"ship": chain("nope")}}},
            "",
            None,
            1,
            "extends no task named 'release:nope'",
        ),
        (
            {"release": {**GOOD["release"], "manifest": {"requires": {"kraft": "3.0"}}}},
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
            "chains/Review.yaml is not part of the plugin layout",
        ),
        (GOOD, "plugins", None, 1, "holds neither"),
    ],
    ids=[
        "collection",
        "single-plugin",
        "lint-failure",
        "wrong-kraft-major",
        "unknown-key-warning",
        "no-home",
        "ignored-look-alike",
        "not-a-plugin",
    ],
)
def test_validate(tmp_path, capsys, monkeypatch, plugins, sub, running, code, says):
    if running is not None:
        monkeypatch.setattr(update, "installed", lambda: running)
    collection = make_collection(tmp_path, plugins)
    got, out = _run(capsys, str(collection / sub))
    assert got == code and says in out, out


def test_validate_writes_nothing_and_prints_json(tmp_path, capsys, monkeypatch):
    """`validate` installs nothing: the instance's home is not touched, with a
    home or without one. `--json` is the report itself."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KRAFT_HOME", str(home))
    collection = make_collection(tmp_path, GOOD)
    before = set(home.rglob("*"))
    code, out = _run(capsys, str(collection), "--json")
    report = json.loads(out)
    assert code == 0 and set(home.rglob("*")) == before
    assert [p["name"] for p in report["plugins"]] == ["release"]
    assert report["plugins"][0]["digest"].startswith("sha256:")
    assert report["problems"] == [] and report["skipped"]
