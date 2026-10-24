"""`kraft.adapters.agent` on a harness with a `min_version` (Kraft-8cml5): an
opencode older than 2.0.0 refuses the argv Kraft builds, so the launch asks
`--version` first and refuses by name instead of failing inside the agent."""

import sys

import pytest
from support.session_leader import SESSION_LEADER_ONLY

from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs


def _opencode(run, tmp_path, answer, **kw):
    cli = tmp_path / "opencode"
    cli.write_text(f"#!/bin/sh\ncat <<'EOF'\n{answer}\nEOF\n")
    cli.chmod(0o755)
    return run(
        harness="opencode",
        command=str(cli),
        cwd=str(tmp_path),
        run_dirs=RunDirs(base=tmp_path / "run"),
        **kw,
    )


@pytest.mark.parametrize(
    ("answer", "read"),
    [
        ("1.18.33", "1.18.33"),
        ("opencode v2.0.0-beta.1", "2.0.0-beta.1"),
        # A wrapper printing its runtime's version first: the CLI's line wins.
        ("node v22.1.0\nopencode-ai 1.18.33", "1.18.33"),
    ],
    ids=["older", "pre-release", "runtime-first"],
)
def test_an_opencode_older_than_its_minimum_is_refused(run, tmp_path, answer, read):
    with pytest.raises(LaunchRefused, match=rf"2\.0\.0 or newer.*machine has {read};"):
        _opencode(run, tmp_path, answer)


@pytest.mark.parametrize("answer", ["opencode v2.0.0", "no version here"])
def test_an_opencode_at_its_minimum_or_unknown_launches(run, tmp_path, answer):
    assert _opencode(run, tmp_path, answer)["cmd"][1] == "run"


def test_a_sandboxed_opencode_is_asked_in_its_image(run, tmp_path, monkeypatch):
    class Image:
        closed = False

        def argv(self):
            # Asked off the terminal: a oneshot's client leads its own session.
            leader = [sys.executable, "-c", SESSION_LEADER_ONLY]
            return [*leader, "sh", "-c", "echo opencode v1.0.4", "sh"]

        async def close(self):
            Image.closed = True

    monkeypatch.setattr("kraft.worker.backends.docker.oneshot", lambda *a: Image())
    with pytest.raises(LaunchRefused, match=r"sandbox image has 1\.0\.4"):
        _opencode(
            run,
            tmp_path,
            "opencode v2.0.15",
            sandbox={"kind": "docker", "image": "x", "unrestricted_network": True},
        )
    assert Image.closed


def test_a_sandboxed_step_on_a_machine_asks_no_image_of_the_server(run, tmp_path, monkeypatch):
    def asked(*a, **kw):
        raise AssertionError("the server's image is not the machine's")

    monkeypatch.setattr("kraft.worker.backends.docker.oneshot", asked)
    seen = _opencode(
        run,
        tmp_path,
        "opencode v1.0.4",
        sandbox={"kind": "docker", "image": "x", "unrestricted_network": True},
        runner="gpu",
    )
    assert seen["runner"] == "gpu" and seen["sandbox"]["kind"] == "docker"
