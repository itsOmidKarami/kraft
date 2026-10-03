"""`kraft doctor`'s rows about connected repos: their paths, setup
commands, keys, forges, steering, and duplicates. The rest: tests/cli/test_doctor.py."""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest
import yaml
from support.harness import make_repo

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _names(rows):
    return [row["name"] for row in rows]


def _by_name(rows, name):
    return next(row for row in rows if row["name"] == name)


def test_a_disconnected_repo_path_fails(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="gone")
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".git").rename(repo / "not-git")
    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("repo "))
    assert not row["ok"]
    assert "no longer a git repo" in row["detail"]


def test_doctor_reports_a_connected_repo_with_no_setup_command(app, tmp_path):
    """Section 1 removes the Python default deliberately, so every connected
    repo needs a declaration. Finding that out from doctor beats finding it
    out from a parked work item."""
    repo = make_repo(tmp_path, name="undeclared")
    asyncio.run(client.ensure_repo(str(repo)))
    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("setup "))
    assert not row["ok"]
    assert "setup_command" in row["detail"]
    assert "tick No setup needed under Templates › Repos" in row["detail"]


@pytest.mark.parametrize(
    ("order", "clone_managed", "flagged"),
    [
        ("member-first", False, True),
        ("clone-first", False, True),
        ("member-first", True, False),
    ],
    ids=["member-first", "clone-first", "two-clones-a-person-connected"],
)
def test_doctor_warns_on_a_member_and_its_detected_stub(
    app, tmp_path, order, clone_managed, flagged
):
    """Kraft-d7aj3: a member connected on its own and a detected stub of it
    (the root's submodule checkout) already both in repos.yaml. Either order:
    the stub's origin is the member. Two clones a person connected are
    deliberate, and not flagged."""
    member = make_repo(tmp_path, name="member")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(member), str(clone)], check=True)
    for repo in (member, clone) if order == "member-first" else (clone, member):
        asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "clone")["managed"] = clone_managed
    path.write_text(yaml.safe_dump(data))

    rows = [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("duplicate ")]

    assert len(rows) == flagged
    if flagged:
        assert rows[0]["warn"], rows[0]
        assert str(member.resolve()) in rows[0]["detail"]
        assert str(clone.resolve()) in rows[0]["detail"]


def test_doctor_fails_a_repo_entry_carrying_an_unrecognised_key(app, tmp_path):
    """Kraft-4hn34: a key that binds nothing is a failing check, naming it."""
    repo = make_repo(tmp_path, name="stale")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    data["repos"][0]["legacy_widget"] = 1
    path.write_text(yaml.safe_dump(data))

    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("keys "))

    assert not row["ok"]
    assert "legacy_widget" in row["detail"]


@pytest.mark.parametrize("retired", ["default_model", "default_root_merge_policy"])
def test_doctor_passes_a_repo_entry_carrying_a_retired_key(app, tmp_path, retired):
    """Ruling 165: an older install's retired keys are dropped on read with a
    warning of their own, so doctor's unrecognised-key check never sees them
    and must not fail on them."""
    repo = make_repo(tmp_path, name="older")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    data["repos"][0][retired] = "bump" if retired.endswith("policy") else "sonnet"
    path.write_text(yaml.safe_dump(data))

    checks = asyncio.run(doctor.run_checks())

    assert not [r for r in checks if r["name"].startswith("keys ")]
    assert any(r["name"].startswith("repo ") and r["ok"] for r in checks)


def test_doctor_fails_when_a_forge_task_has_no_forge_recorded(app, tmp_path):
    """make_repo never adds an origin, so `probe_repo` records forge: None —
    the state every pre-forge repos.yaml entry is already in."""
    repo = make_repo(tmp_path, name="noforge")
    asyncio.run(client.ensure_repo(str(repo)))

    row = _by_name(asyncio.run(doctor.run_checks()), "forge noforge")

    assert not row["ok"]
    assert "repos.yaml" in row["detail"]


def test_doctor_reports_the_resolved_forge_cli(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="onforge")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@gitlab.com:group/repo.git"],
        cwd=repo,
        check=True,
    )
    asyncio.run(client.ensure_repo(str(repo)))
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    glab = stub_dir / "glab"
    glab.write_text("#!/bin/sh\nexit 0\n")
    glab.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    row = _by_name(asyncio.run(doctor.run_checks()), "forge onforge")

    assert row["ok"]
    assert row["detail"] == "gitlab · glab"


def test_doctor_warns_on_a_repo_on_the_dev_only_fake_forge(app, tmp_path):
    """Ruling 147: `forge: fake` resolves to the in-process `FakeForge`, so
    there is no `fake` binary to look for on PATH -- failing the check would
    tell a `just dev` user their forge is broken when it is not. But green
    would hide a real repo left on a forge that opens nothing and merges
    nothing (review finding 5), so it is a visible warning."""
    repo = make_repo(tmp_path, name="devforge")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "devforge")["forge"] = "fake"
    path.write_text(yaml.safe_dump(data))

    row = _by_name(asyncio.run(doctor.run_checks()), "forge devforge")

    assert row["warn"], row
    assert "fake" in row["detail"] and "dev only" in row["detail"]
    assert "nothing" in row["detail"]


def test_no_forge_check_when_no_chain_runs_a_forge_task(app, tmp_path):
    """The check is about forge work the operator's chains actually do: a
    library whose chains run no forge task must not grow a row per repo telling
    them to fix something they are not using. `quick-task` runs none."""
    repo = make_repo(tmp_path, name="quiet")
    asyncio.run(client.ensure_repo(str(repo)))
    assert "forge quiet" in _names(asyncio.run(doctor.run_checks()))
    (tmp_path / "templates" / "chains" / "default.yaml").unlink()

    names = _names(asyncio.run(doctor.run_checks()))

    assert "repo quiet" in names
    assert "forge quiet" not in names


def test_doctor_fails_a_repo_naming_a_steering_profile_the_library_lacks(app, tmp_path):
    """Kraft-v7u1f: repo save, intake, and a library save removing a named
    profile all refuse a repos.yaml `steering:` name the library does not
    define -- but a hand-edited repos.yaml (or a library edited outside
    Kraft) reached those refusals only at the next intake's 422. Doctor
    needs its own row, naming the repo and the missing name, reusing the
    same resolution those refusals use."""
    repo = make_repo(tmp_path, name="unsteered")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "unsteered")["steering"] = ["gone"]
    path.write_text(yaml.safe_dump(data))

    row = _by_name(asyncio.run(doctor.run_checks()), "steering unsteered")

    assert not row["ok"]
    assert "gone" in row["detail"]
    assert "unsteered" in row["detail"] or str(repo) in row["detail"]


def test_doctor_passes_a_repo_naming_a_steering_profile_the_library_defines(app, tmp_path):
    repo = make_repo(tmp_path, name="steered")
    library_path = tmp_path / "templates" / "library.yaml"
    library_path.write_text(
        library_path.read_text().replace(
            "steering:\n", "steering:\n  house:\n    instructions: x\n", 1
        )
    )
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "steered")["steering"] = ["house"]
    path.write_text(yaml.safe_dump(data))

    row = _by_name(asyncio.run(doctor.run_checks()), "steering steered")

    assert row["ok"], row


def _set_entry(tmp_path, **values):
    """Overwrite the first repos.yaml entry's keys, as a 1.4 install left them."""
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    data["repos"][0].update(values)
    path.write_text(yaml.safe_dump(data))


def test_doctor_says_an_empty_test_command_runs_no_tests(app, tmp_path):
    """1.4 stopped such a repo's items at verify; now they pass it with no test
    run, and nothing a person looks at said so."""
    repo = make_repo(tmp_path, name="emptytest")
    asyncio.run(client.ensure_repo(str(repo)))
    _set_entry(tmp_path, test_command="", test_scopes=None)

    row = _by_name(asyncio.run(doctor.run_checks()), "tests emptytest")

    assert row["warn"], row
    assert 'test_command ""' in row["detail"]
    assert "pass verify without running a test" in row["detail"]


def test_doctor_names_an_entry_from_before_the_probe_and_says_to_connect_again(app, tmp_path):
    """An entry connected before this release's probe can leave both commands
    undecided where the probe now finds them; connecting again saves them."""
    repo = make_repo(tmp_path, name="older")
    (repo / "Makefile").write_text("setup:\n\ttrue\ntest:\n\ttrue\n")
    subprocess.run(["git", "-C", str(repo), "add", "Makefile"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "make"], check=True)
    asyncio.run(client.ensure_repo(str(repo)))
    _set_entry(tmp_path, test_command=None, test_scopes=None, setup_command=None)

    rows = asyncio.run(doctor.run_checks())

    again = f"run `kraft repo connect {repo}` again to save it"
    tests = _by_name(rows, "tests older")
    assert tests["warn"] and "stop at verify" in tests["detail"], tests
    assert "`make test`" in tests["detail"] and again in tests["detail"]
    assert again in _by_name(rows, "setup older")["detail"]
