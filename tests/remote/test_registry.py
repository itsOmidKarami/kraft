"""`workers.yaml`: exactly one connection mode per machine, names that cannot
collide with a placement value, tokens kept only as hashes."""

from __future__ import annotations

import pytest

from kraft.config import ConfigError, RepoEntry
from kraft.remote import registry

SHA = "a" * 64


def _load(tmp_path, text):
    path = tmp_path / "workers.yaml"
    path.write_text(text)
    return registry.WorkersFile.load(path)


def test_a_missing_file_is_no_machines(tmp_path):
    assert registry.WorkersFile.load(tmp_path / "workers.yaml").workers == {}


def test_both_modes_load_with_their_defaults(tmp_path):
    loaded = _load(
        tmp_path,
        f"workers:\n  gpu-box:\n    ssh: gpu-box\n    capacity: 2\n    repos: [kraft]\n"
        f"  home-server:\n    token_sha256: {SHA}\n    give_up_after: never\n",
    )
    gpu, home = loaded.workers["gpu-box"], loaded.workers["home-server"]
    assert (gpu.mode, gpu.capacity, gpu.repos, gpu.give_up_s) == ("ssh", 2, ["kraft"], 7200)
    assert (home.mode, home.capacity, home.repos, home.give_up_s) == ("dial-in", 1, None, None)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("workers:\n  a:\n    capacity: 1\n", "exactly one of"),
        (f"workers:\n  a:\n    ssh: h\n    token_sha256: {SHA}\n", "exactly one of"),
        ("workers:\n  a:\n    ssh: h\n    capacity: 0\n", "capacity"),
        ("workers:\n  any:\n    ssh: h\n", "reserved"),
        ("workers:\n  local:\n    ssh: h\n", "reserved"),
        ("workers:\n  Gpu Box:\n    ssh: h\n", "name"),
        ("workers:\n  a:\n    ssh: '-oProxyCommand'\n", "ssh"),
        ("workers:\n  a:\n    ssh: 'h; rm -rf /'\n", "ssh"),
        ("workers:\n  a:\n    ssh: h\n    give_up_after: soon\n", "give_up_after"),
        ("workers:\n  a:\n    ssh: h\n    give_up_after: 0h\n", "give_up_after"),
        ("workers:\n  a:\n    token_sha256: nothex\n", "token_sha256"),
        ("workers:\n  a:\n    ssh: h\n    colour: red\n", "colour"),
    ],
    ids=[
        "neither-mode",
        "both-modes",
        "capacity-zero",
        "reserved-any",
        "reserved-local",
        "bad-name",
        "ssh-option-injection",
        "ssh-shell-metacharacters",
        "give-up-word",
        "give-up-zero",
        "token-not-hex",
        "unknown-key",
    ],
)
def test_a_bad_file_is_refused_naming_the_problem(tmp_path, text, why):
    with pytest.raises(ConfigError, match=why):
        _load(tmp_path, text)


def test_an_empty_workers_key_is_no_machines(tmp_path):
    assert _load(tmp_path, "workers:\n").workers == {}


def test_a_token_finds_its_machine_and_nothing_else(tmp_path):
    token = registry.new_token()
    assert token.startswith("kwt_") and len(token) > 30
    loaded = _load(
        tmp_path,
        f"workers:\n  home:\n    token_sha256: {registry.hash_token(token)}\n"
        f"  other:\n    token_sha256: {registry.hash_token('kwt_else')}\n  boxed:\n    ssh: h\n",
    )
    assert registry.find_by_token(loaded, token) == "home"
    assert registry.find_by_token(loaded, "kwt_wrong") is None
    assert registry.find_by_token(loaded, "") is None
    assert registry.hash_token(token) != token


@pytest.mark.parametrize(
    ("entry", "name", "matches"),
    [
        ({"path": "/r/kraft", "id": "kraft-core"}, "kraft-core", True),
        ({"path": "/r/kraft", "name": "Kraft"}, "Kraft", True),
        ({"path": "/r/kraft"}, "kraft", True),
        ({"path": "/r/kraft"}, "infra", False),
    ],
    ids=["by-id", "by-name", "by-path-basename", "unknown"],
)
def test_a_repo_name_matches_a_connected_repo(entry, name, matches):
    assert registry.repo_matches(RepoEntry.model_validate(entry), name) is matches
