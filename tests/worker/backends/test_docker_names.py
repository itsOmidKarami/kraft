"""A session's Docker names: its item and its id, or, for a session from
before per-item ids, its id alone, as it started (spec "Item files and IDs",
part 3)."""

from __future__ import annotations

import pytest

from kraft.worker.backends import docker

#: A session id minted before per-item ids.
OLD = "8cbfe6e27c1044b3e445c0f0d726357d"


@pytest.mark.parametrize(
    ("fn", "prefix"),
    [
        (docker.container_name, "kraft-"),
        (docker.relay_name, "kraft-relay-"),
        (docker.relay_b_name, "kraft-relay-b-"),
        (docker.volume_name, "kraft-egress-"),
    ],
    ids=["container", "relay", "relay-b", "volume"],
)
def test_a_session_s_docker_names_carry_its_item(fn, prefix):
    """Docker's names are the whole daemon's, another Kraft instance's
    sessions included, and a session id is unique only within its item."""
    assert fn("k7m2x9qa", "p3n8w2") == f"{prefix}k7m2x9qa-p3n8w2"
    assert fn("k7m2x9qa", "p3n8w2") != fn("b4t1z8re", "p3n8w2")
    assert fn("k7m2x9qa", OLD) == f"{prefix}{OLD}"


@pytest.mark.parametrize(
    ("wid", "sid", "name", "relay"),
    [
        ("k7m2x9qa", "p3n8w2", "kraft-k7m2x9qa-p3n8w2", "kraft-relay-k7m2x9qa-p3n8w2"),
        ("k7m2x9qa", OLD, f"kraft-{OLD}", f"kraft-relay-{OLD}"),
    ],
    ids=["per-item-session", "older-session"],
)
def test_a_launch_runs_and_joins_the_names_its_session_has(wid, sid, name, relay):
    """What `wrap` launches under is what `close`, `oom_killed` and the sweep
    look for: the container's name, and under `network:` the relay it joins."""
    sandbox = {"kind": "docker", "image": "img", "network": {"runtime": {"allow": ["a.io"]}}}

    argv = docker.DockerBackend().wrap(
        ["true"], "/w", sandbox, None, session_id=sid, work_item_id=wid
    )

    assert argv[argv.index("--name") + 1] == name
    assert f"--network=container:{relay}" in argv
