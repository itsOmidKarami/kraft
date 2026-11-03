"""`kraft admin addon` and the built-in `kraft-default@kraft`: the verbs leave
it alone, and what builds on it installs without a collection to hold it."""

import shutil

import pytest
from support.addons import (
    AGENT,
    addon_cli_home,
    chain,
    child_manifest,
    install,
    make_collection,
    publish,
    run_addon_cli,
)

from kraft.addons import builtin
from kraft.config import read_yaml, write_yaml

SDLC = "kraft-default@sdlc"
KID = {"library": {"tasks": {"k": AGENT}}, "chains": {"go": chain("k")}}
ON_BUILTIN = {**KID, "manifest": child_manifest(builtin.ID, "2")}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return addon_cli_home(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "argv",
    [
        ["install"],
        ["update"],
        ["update", "--check"],
        ["uninstall"],
        ["enable"],
        ["disable"],
        ["auto-update"],
    ],
    ids=lambda a: " ".join(a),
)
def test_the_builtin_is_not_an_addon_verbs_to_change(home, capsys, argv):
    before = home.written()
    argv = [*argv, builtin.ID, *(["on"] if argv == ["auto-update"] else [])]
    code, out = run_addon_cli(capsys, *argv)
    assert code == 1 and builtin.REFUSED in out
    assert home.written() == before and home.reloads == []


def test_the_refusal_comes_before_the_worker_check(home, capsys, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    code, out = run_addon_cli(capsys, "update", builtin.ID)
    assert code == 1 and builtin.REFUSED in out and "worker" not in out


@pytest.mark.parametrize("flags", [["--check"], ["-y"]], ids=["check", "apply"])
def test_a_bare_update_skips_the_builtin_silently(home, capsys, flags):
    code, out = run_addon_cli(capsys, "update", *flags)
    assert code == 0 and builtin.REFUSED not in out and builtin.ID not in out


def test_a_collection_named_kraft_is_refused(tmp_path, home, capsys):
    repo = make_collection(tmp_path / "x", {"kid": KID}, name=builtin.COLLECTION)
    code, out = run_addon_cli(capsys, "collection", "add", repo.as_uri())
    assert code == 1 and builtin.COLLECTION_REFUSED in out
    assert builtin.COLLECTION not in home.yaml()["collections"]


def test_moving_a_2_5_home_off_its_github_copy(tmp_path, home, capsys):
    parent = {"library": {"tasks": {"base": AGENT}}}
    child = {**KID, "manifest": child_manifest(SDLC, "1")}
    repo = make_collection(tmp_path / "x", {"kraft-default": parent, "kid": child}, name="sdlc")
    install(home.config, home.addons, repo, "kraft-default")
    install(home.config, home.addons, repo, "kid")
    write_yaml(home.config / "chains" / "default.yaml", {"extends": "kraft-default:default"})
    publish(repo, "kid", **{**KID, "manifest": child_manifest(builtin.ID, "2")}, version="1.1.0")

    code, out = run_addon_cli(capsys, "update", "kid@sdlc", "-y")
    assert code == 0 and "applied" in out, out
    assert home.locked("kid@sdlc").version == "1.1.0"

    code, out = run_addon_cli(capsys, "uninstall", SDLC)
    assert code == 0, out
    assert SDLC not in home.yaml()["addons"]


@pytest.mark.parametrize("case", ["no-collection", "no-claude-harness"])
def test_a_child_of_the_builtin_installs(tmp_path, home, capsys, case):
    repo = make_collection(tmp_path / "x", {"kid": ON_BUILTIN})
    assert builtin.COLLECTION not in home.yaml()["collections"]  # never added, in either case
    harnesses = read_yaml(home.config / "harnesses.yaml")["harnesses"]
    if case == "no-claude-harness":
        assert set(harnesses) == {"codex"}  # what `instance` writes
    else:  # the harness is there; only the collection is not
        shutil.copy(builtin.source_dir() / "harnesses.yaml", home.config / "harnesses.yaml")
    assert run_addon_cli(capsys, "collection", "add", repo.as_uri())[0] == 0
    code, out = run_addon_cli(capsys, "install", "kid@acme", "-y")
    assert code == 0 and "applied" in out, out
    assert "kid@acme" in home.yaml()["addons"]


@pytest.mark.parametrize("where", ["with-home", "without-home"])
def test_validate_finds_the_builtin_parent(tmp_path, home, capsys, monkeypatch, where):
    repo = make_collection(tmp_path / "x", {"kid": ON_BUILTIN})
    if where == "without-home":
        monkeypatch.delenv("KRAFT_CONFIG_DIR")
        monkeypatch.delenv("KRAFT_RUN_DIR")
        monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "empty"))
    code, out = run_addon_cli(capsys, "validate", str(repo))
    assert code == 0 and "error" not in out and "could not be found" not in out, out


def test_enable_writes_the_missing_builtin_store_outside_the_lock(
    tmp_path, home, capsys, monkeypatch
):
    from kraft.addons import update

    monkeypatch.setattr(update, "LOCK_WAIT_S", 0.3)  # the bug is a wait; keep it short
    repo = make_collection(tmp_path / "x", {"kid": ON_BUILTIN})
    install(home.config, home.addons, repo, "kid")
    raw = read_yaml(home.config / "addons.yaml")
    raw["addons"]["kid@acme"] = {"enabled": False}
    write_yaml(home.config / "addons.yaml", raw)
    shutil.rmtree(home.addons / "store" / builtin.store_name(), ignore_errors=True)

    code, out = run_addon_cli(capsys, "enable", "kid@acme")
    assert code == 0, out
    assert "another add-on change is running" not in out
